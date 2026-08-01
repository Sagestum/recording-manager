"""Erkennung und Aufloesung von Aufnahme-Konflikten (Tuner-Anzahl + CI+/CAM-Limit)."""

import logging
import time

from . import db, enigma_client
from .matching import find_matching_pattern

log = logging.getLogger("conflicts")


def build_channel_lookup(channels):
    by_ref = {c["service_ref"]: bool(c["encrypted"]) for c in channels}
    by_name = {c["name"].strip().lower(): bool(c["encrypted"]) for c in channels}
    return by_ref, by_name


def is_encrypted(service_ref, servicename, by_ref, by_name):
    if service_ref in by_ref:
        return by_ref[service_ref]
    return by_name.get((servicename or "").strip().lower(), False)


def get_priority(name, patterns, default_priority):
    match = find_matching_pattern(name, patterns)
    if match:
        return match["priority"], match["pattern"]
    return default_priority, None


def build_timer_items(timers, patterns, channels, default_priority):
    by_ref, by_name = build_channel_lookup(channels)
    now = int(time.time())
    items = []
    for t in timers:
        if t.get("disabled"):
            continue
        end = t.get("end") or 0
        if end <= now:
            continue
        name = t.get("name") or ""
        servicename = t.get("servicename") or ""
        serviceref = t.get("serviceref") or ""
        priority, matched_pattern = get_priority(name, patterns, default_priority)
        items.append(
            {
                "name": name,
                "servicename": servicename,
                "serviceref": serviceref,
                "begin": t.get("begin") or 0,
                "end": end,
                "priority": priority,
                "matched_pattern": matched_pattern,
                "encrypted": is_encrypted(serviceref, servicename, by_ref, by_name),
                "raw": t,
            }
        )
    items.sort(key=lambda i: i["begin"])
    return items


def build_clusters(items):
    clusters = []
    current = []
    current_end = None
    for item in items:
        if current and item["begin"] < current_end:
            current.append(item)
            current_end = max(current_end, item["end"])
        else:
            if len(current) > 1:
                clusters.append(current)
            current = [item]
            current_end = item["end"]
    if len(current) > 1:
        clusters.append(current)
    return clusters


def sweep_peak(items):
    """Ermittelt die maximale Anzahl gleichzeitig aktiver, unterschiedlicher Sender
    sowie die maximale Anzahl gleichzeitig aktiver, unterschiedlicher verschluesselter Sender."""
    events = []
    for item in items:
        events.append((item["begin"], 1, id(item), item))
        events.append((item["end"], 0, id(item), item))
    events.sort(key=lambda e: (e[0], e[1]))

    active = {}
    max_channels = 0
    max_encrypted = 0
    for _, kind, key, item in events:
        if kind == 1:
            active[key] = item
        else:
            active.pop(key, None)
        distinct_refs = {}
        for a in active.values():
            distinct_refs[a["serviceref"]] = a
        max_channels = max(max_channels, len(distinct_refs))
        encrypted_refs = [ref for ref, a in distinct_refs.items() if a["encrypted"]]
        max_encrypted = max(max_encrypted, len(encrypted_refs))
    return max_channels, max_encrypted


def resolve_cluster(cluster, tuner_count):
    """Greedy: hoechste Prioritaet zuerst behalten, solange Tuner- und CAM-Limit eingehalten werden."""
    ordered = sorted(cluster, key=lambda i: (-i["priority"], i["begin"]))
    kept = []
    disabled = []
    for item in ordered:
        trial = kept + [item]
        max_channels, max_encrypted = sweep_peak(trial)
        if max_channels <= tuner_count and max_encrypted <= 1:
            kept.append(item)
        else:
            disabled.append(item)
    return kept, disabled


def analyze(timers, patterns, channels, tuner_count, default_priority):
    items = build_timer_items(timers, patterns, channels, default_priority)
    clusters = build_clusters(items)

    results = []
    for cluster in clusters:
        max_channels, max_encrypted = sweep_peak(cluster)
        tuner_problem = max_channels > tuner_count
        cam_problem = max_encrypted > 1
        conflict = tuner_problem or cam_problem
        kept, disabled = resolve_cluster(cluster, tuner_count) if conflict else (cluster, [])
        results.append(
            {
                "timers": cluster,
                "begin": min(i["begin"] for i in cluster),
                "end": max(i["end"] for i in cluster),
                "max_channels": max_channels,
                "max_encrypted": max_encrypted,
                "tuner_problem": tuner_problem,
                "cam_problem": cam_problem,
                "conflict": conflict,
                "keep": kept,
                "disable": disabled,
            }
        )
    results.sort(key=lambda r: r["begin"])
    return results


def run_conflict_check():
    log.info("Starte Konflikt-Check")
    try:
        timers = enigma_client.get_timerlist()
    except Exception as exc:
        log.error("Timerlist konnte nicht geladen werden: %s", exc)
        db.set_setting("last_conflict_check_at", str(int(time.time())))
        db.set_setting(
            "last_conflict_check_summary", f"Fehler: Timerlist konnte nicht geladen werden ({exc})"
        )
        return

    patterns = db.list_patterns()
    channels = db.list_channels()
    tuner_count = int(db.get_setting("tuner_count", "2") or "2")
    default_priority = int(db.get_setting("default_priority", "0") or "0")
    auto_resolve = db.get_setting("conflict_auto_resolve", "0") == "1"

    results = analyze(timers, patterns, channels, tuner_count, default_priority)
    conflicts = [r for r in results if r["conflict"]]

    disabled_count = errors = 0
    if auto_resolve:
        for cluster in conflicts:
            for item in cluster["disable"]:
                if item["raw"].get("disabled"):
                    continue
                reason = (
                    f"Konflikt mit {', '.join(k['name'] for k in cluster['keep'])} "
                    f"(Tuner: {cluster['max_channels']}/{tuner_count}, "
                    f"verschl. Sender gleichzeitig: {cluster['max_encrypted']})"
                )
                try:
                    ok, message = enigma_client.set_timer_disabled(item["raw"], disabled=True)
                except Exception as exc:
                    ok, message = False, str(exc)
                db.log_conflict_action(
                    item["name"],
                    item["servicename"],
                    item["begin"],
                    item["end"],
                    item["priority"],
                    reason,
                    ok,
                    message,
                )
                if ok:
                    disabled_count += 1
                    log.info("Timer deaktiviert: %s (%s)", item["name"], reason)
                else:
                    errors += 1
                    log.warning("Deaktivieren fehlgeschlagen fuer %s: %s", item["name"], message)

    summary = f"{len(conflicts)} Konflikt(e) gefunden"
    if auto_resolve:
        summary += f", {disabled_count} Timer automatisch deaktiviert, {errors} Fehler"
    db.set_setting("last_conflict_check_at", str(int(time.time())))
    db.set_setting("last_conflict_check_summary", summary)
    log.info(summary)
