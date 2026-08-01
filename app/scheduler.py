import logging
import re
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

from . import db, enigma_client

log = logging.getLogger("scheduler")

_scheduler = BackgroundScheduler()
_job = None


def pattern_matches(pattern_row, eventname):
    text = eventname or ""
    if pattern_row["is_regex"]:
        try:
            return re.search(pattern_row["pattern"], text, re.IGNORECASE) is not None
        except re.error:
            log.warning("Ungueltiges Regex-Muster ignoriert: %s", pattern_row["pattern"])
            return False
    return pattern_row["pattern"].lower() in text.lower()


def find_matching_pattern(eventname, patterns):
    for p in patterns:
        if not p["enabled"]:
            continue
        if pattern_matches(p, eventname):
            return p
    return None


def run_check():
    """Aufnahmen laden, abgelaufene (nach Muster-Regel) loeschen."""
    log.info("Starte Aufraeum-Check")
    checked = deleted = errors = 0

    try:
        movies = enigma_client.get_movielist()
    except Exception as exc:  # Box nicht erreichbar o.ae.
        log.error("Movielist konnte nicht geladen werden: %s", exc)
        db.set_setting("last_check_at", str(int(time.time())))
        db.set_setting("last_check_summary", f"Fehler: Movielist konnte nicht geladen werden ({exc})")
        return

    patterns = db.list_patterns()
    now = int(time.time())

    for movie in movies:
        checked += 1
        eventname = movie.get("eventname") or ""
        match = find_matching_pattern(eventname, patterns)
        if not match:
            continue

        recordingtime = movie.get("recordingtime") or 0
        expiry = recordingtime + match["days"] * 86400
        if now < expiry:
            continue

        service_ref = movie.get("serviceref") or movie.get("fullname")
        if not service_ref:
            continue

        try:
            ok, message = enigma_client.delete_movie(service_ref)
        except Exception as exc:
            ok, message = False, str(exc)

        db.log_deletion(eventname, movie.get("filename", ""), match["pattern"], recordingtime, ok, message)

        if ok:
            deleted += 1
            log.info("Geloescht: %s (Muster '%s')", eventname, match["pattern"])
        else:
            errors += 1
            log.warning("Loeschen fehlgeschlagen fuer %s: %s", eventname, message)

    summary = f"{checked} Aufnahmen geprueft, {deleted} geloescht, {errors} Fehler"
    db.set_setting("last_check_at", str(now))
    db.set_setting("last_check_summary", summary)
    log.info(summary)


def _interval_hours():
    try:
        return max(1, int(float(db.get_setting("check_interval_hours", "6"))))
    except (TypeError, ValueError):
        return 6


def reschedule():
    global _job
    hours = _interval_hours()
    _job = _scheduler.add_job(
        run_check, "interval", hours=hours, id="recording_check", replace_existing=True
    )
    log.info("Hintergrund-Job neu geplant: alle %s Stunden", hours)


def next_run_time():
    return _job.next_run_time if _job is not None else None


def run_check_async():
    threading.Thread(target=run_check, daemon=True).start()


def start():
    if not _scheduler.running:
        _scheduler.start()
    reschedule()
    run_check_async()
