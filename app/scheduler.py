import logging
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

from . import db, enigma_client
from .matching import (  # noqa: F401 (re-exported for main.py / templates)
    DEFAULT_RULE_LABEL,
    find_matching_pattern,
    get_default_retention_days,
    pattern_matches,
    resolve_rule,
)

log = logging.getLogger("scheduler")

_scheduler = BackgroundScheduler()
_job = None
_conflict_job = None


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
        pattern_label, days = resolve_rule(eventname, patterns)
        if days is None:
            continue

        recordingtime = movie.get("recordingtime") or 0
        expiry = recordingtime + days * 86400
        if now < expiry:
            continue

        service_ref = movie.get("serviceref") or movie.get("fullname")
        if not service_ref:
            continue

        try:
            ok, message = enigma_client.delete_movie(service_ref)
        except Exception as exc:
            ok, message = False, str(exc)

        db.log_deletion(eventname, movie.get("filename", ""), pattern_label, recordingtime, ok, message)

        if ok:
            deleted += 1
            log.info("Geloescht: %s (Regel '%s')", eventname, pattern_label)
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


def _conflict_interval_minutes():
    try:
        return max(5, int(float(db.get_setting("conflict_check_interval_minutes", "30"))))
    except (TypeError, ValueError):
        return 30


def reschedule():
    global _job
    hours = _interval_hours()
    _job = _scheduler.add_job(
        run_check, "interval", hours=hours, id="recording_check", replace_existing=True
    )
    log.info("Aufraeum-Job neu geplant: alle %s Stunden", hours)


def reschedule_conflicts():
    global _conflict_job
    from . import conflicts  # spaeter Import verhindert Zirkularitaet beim Modul-Laden

    minutes = _conflict_interval_minutes()
    _conflict_job = _scheduler.add_job(
        conflicts.run_conflict_check,
        "interval",
        minutes=minutes,
        id="conflict_check",
        replace_existing=True,
    )
    log.info("Konflikt-Job neu geplant: alle %s Minuten", minutes)


def next_run_time():
    return _job.next_run_time if _job is not None else None


def next_conflict_run_time():
    return _conflict_job.next_run_time if _conflict_job is not None else None


def run_check_async():
    threading.Thread(target=run_check, daemon=True).start()


def run_conflict_check_async():
    from . import conflicts

    threading.Thread(target=conflicts.run_conflict_check, daemon=True).start()


def start():
    if not _scheduler.running:
        _scheduler.start()
    reschedule()
    reschedule_conflicts()
    run_check_async()
    run_conflict_check_async()
