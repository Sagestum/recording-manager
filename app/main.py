import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import conflicts, db, enigma_client, scheduler
from .config import ENIGMA_HOST, ENIGMA_PORT, MANAGER_PASS, MANAGER_USER, TZ_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
TZ = ZoneInfo(TZ_NAME)

security = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not MANAGER_USER:
        return True
    valid = credentials is not None and secrets.compare_digest(
        credentials.username, MANAGER_USER
    ) and secrets.compare_digest(credentials.password, MANAGER_PASS)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht autorisiert",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


app = FastAPI(title="Enigma2 Recording Manager")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def fmt_ts(ts):
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(int(ts), TZ).strftime("%d.%m.%Y %H:%M")
    except (ValueError, OSError):
        return "-"


templates.env.filters["fmt_ts"] = fmt_ts


@app.on_event("startup")
def on_startup():
    db.init_db()
    scheduler.start()


@app.get("/")
def root():
    return RedirectResponse(url="/recordings")


@app.get("/recordings")
def recordings_page(request: Request, auth=Depends(require_auth)):
    patterns = db.list_patterns()
    error = None
    movies = []
    try:
        movies = enigma_client.get_movielist()
    except Exception as exc:
        error = str(exc)

    now = int(datetime.now(TZ).timestamp())
    rows = []
    for m in movies:
        eventname = m.get("eventname") or ""
        pattern_label, days = scheduler.resolve_rule(eventname, patterns)
        expiry = (m.get("recordingtime") or 0) + days * 86400 if days else None
        serviceref = m.get("serviceref") or m.get("fullname") or ""
        rows.append(
            {
                "eventname": eventname,
                "servicename": m.get("servicename", ""),
                "recordingtime": m.get("recordingtime"),
                "filesize_readable": m.get("filesize_readable", ""),
                "length": m.get("length", ""),
                "serviceref": serviceref,
                "row_value": f"{serviceref}\x1f{eventname}",
                "pattern": pattern_label,
                "days": days,
                "expiry": expiry,
                "expired": bool(expiry and now >= expiry),
            }
        )
    rows.sort(key=lambda r: r["recordingtime"] or 0, reverse=True)

    return templates.TemplateResponse(
        "recordings.html",
        {"request": request, "rows": rows, "error": error, "active": "recordings"},
    )


@app.post("/recordings/delete")
def delete_recording(
    service_ref: str = Form(...), eventname: str = Form(""), auth=Depends(require_auth)
):
    try:
        ok, message = enigma_client.delete_movie(service_ref)
    except Exception as exc:
        ok, message = False, str(exc)
    db.log_deletion(eventname, "", "manuell", None, ok, message)
    return RedirectResponse(url="/recordings", status_code=303)


@app.post("/recordings/bulk-delete")
def bulk_delete_recordings(selected: List[str] = Form(default=[]), auth=Depends(require_auth)):
    for entry in selected:
        service_ref, _, eventname = entry.partition("\x1f")
        if not service_ref:
            continue
        try:
            ok, message = enigma_client.delete_movie(service_ref)
        except Exception as exc:
            ok, message = False, str(exc)
        db.log_deletion(eventname, "", "manuell (Mehrfachauswahl)", None, ok, message)
    return RedirectResponse(url="/recordings", status_code=303)


@app.get("/timers")
def timers_page(request: Request, auth=Depends(require_auth)):
    error = None
    timers = []
    try:
        timers = enigma_client.get_timerlist()
    except Exception as exc:
        error = str(exc)

    patterns = db.list_patterns()
    channels = db.list_channels()
    by_ref, by_name = conflicts.build_channel_lookup(channels)
    default_priority = int(db.get_setting("default_priority", "50") or "50")

    now = int(datetime.now(TZ).timestamp())
    rows = []
    for t in timers:
        begin = t.get("begin") or 0
        end = t.get("end") or 0
        if t.get("disabled"):
            label = "deaktiviert"
        elif begin <= now <= end:
            label = "läuft"
        elif begin > now:
            label = "geplant"
        else:
            label = "abgeschlossen"
        name = t.get("name", "")
        servicename = t.get("servicename", "")
        serviceref = t.get("serviceref", "")
        priority, _ = conflicts.get_priority(name, patterns, default_priority)
        rows.append(
            {
                "name": name,
                "servicename": servicename,
                "begin": begin,
                "end": end,
                "status": label,
                "disabled": bool(t.get("disabled")),
                "priority": priority,
                "encrypted": conflicts.is_encrypted(serviceref, servicename, by_ref, by_name),
                "toggle_fields": _timer_toggle_fields(t),
            }
        )
    rows.sort(key=lambda r: r["begin"])

    return templates.TemplateResponse(
        "timers.html", {"request": request, "rows": rows, "error": error, "active": "timers"}
    )


def _timer_toggle_fields(t):
    return {
        "serviceref": t.get("serviceref", ""),
        "begin": t.get("begin", 0),
        "end": t.get("end", 0),
        "name": t.get("name", ""),
        "description": t.get("description", ""),
        "eit": t.get("eit", 0),
        "justplay": t.get("justplay", 0),
        "repeated": t.get("repeated", 0),
        "disabled": int(bool(t.get("disabled"))),
    }


@app.post("/timers/toggle")
def toggle_timer(
    request: Request,
    serviceref: str = Form(...),
    begin: int = Form(...),
    end: int = Form(...),
    name: str = Form(""),
    description: str = Form(""),
    eit: int = Form(0),
    justplay: int = Form(0),
    repeated: int = Form(0),
    disabled: int = Form(...),
    redirect_to: str = Form("/timers"),
    auth=Depends(require_auth),
):
    timer = {
        "serviceref": serviceref,
        "begin": begin,
        "end": end,
        "name": name,
        "description": description,
        "eit": eit,
        "justplay": justplay,
        "repeated": repeated,
    }
    new_disabled = not bool(disabled)
    try:
        ok, message = enigma_client.set_timer_disabled(timer, new_disabled)
    except Exception as exc:
        ok, message = False, str(exc)
    db.log_conflict_action(
        name, "", begin, end, None, "manuell" + (" deaktiviert" if new_disabled else " aktiviert"), ok, message
    )
    return RedirectResponse(url=redirect_to, status_code=303)


@app.get("/patterns")
def patterns_page(request: Request, auth=Depends(require_auth)):
    patterns = db.list_patterns()
    return templates.TemplateResponse(
        "patterns.html", {"request": request, "patterns": patterns, "active": "patterns"}
    )


@app.post("/patterns")
def create_pattern(
    pattern: str = Form(...),
    days: int = Form(...),
    is_regex: bool = Form(False),
    priority: int = Form(0),
    auth=Depends(require_auth),
):
    if pattern.strip():
        db.add_pattern(pattern, days, is_regex, True, priority)
    return RedirectResponse(url="/patterns", status_code=303)


@app.post("/patterns/{pattern_id}/update")
def update_pattern_route(
    pattern_id: int,
    pattern: str = Form(...),
    days: int = Form(...),
    is_regex: bool = Form(False),
    enabled: bool = Form(False),
    priority: int = Form(0),
    auth=Depends(require_auth),
):
    db.update_pattern(pattern_id, pattern, days, is_regex, enabled, priority)
    return RedirectResponse(url="/patterns", status_code=303)


@app.post("/patterns/{pattern_id}/delete")
def delete_pattern_route(pattern_id: int, auth=Depends(require_auth)):
    db.delete_pattern(pattern_id)
    return RedirectResponse(url="/patterns", status_code=303)


@app.get("/log")
def log_page(request: Request, auth=Depends(require_auth)):
    entries = db.list_deletion_log()
    return templates.TemplateResponse(
        "log.html", {"request": request, "entries": entries, "active": "log"}
    )


@app.get("/settings")
def settings_page(request: Request, auth=Depends(require_auth)):
    status_info = None
    status_error = None
    try:
        status_info = enigma_client.get_status()
    except Exception as exc:
        status_error = str(exc)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "interval": db.get_setting("check_interval_hours", "6"),
            "default_retention_days": db.get_setting("default_retention_days", "0"),
            "default_priority": db.get_setting("default_priority", "50"),
            "last_check_at": db.get_setting("last_check_at", ""),
            "last_check_summary": db.get_setting("last_check_summary", ""),
            "next_run": scheduler.next_run_time(),
            "enigma_host": ENIGMA_HOST,
            "enigma_port": ENIGMA_PORT,
            "status_info": status_info,
            "status_error": status_error,
            "active": "settings",
        },
    )


@app.post("/settings")
def update_settings(
    interval_hours: int = Form(...),
    default_retention_days: int = Form(0),
    default_priority: int = Form(50),
    auth=Depends(require_auth),
):
    db.set_setting("check_interval_hours", max(1, interval_hours))
    db.set_setting("default_retention_days", max(0, default_retention_days))
    db.set_setting("default_priority", default_priority)
    scheduler.reschedule()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/run-now")
def run_now(auth=Depends(require_auth)):
    scheduler.run_check_async()
    return RedirectResponse(url="/settings", status_code=303)


@app.get("/channels")
def channels_page(request: Request, auth=Depends(require_auth)):
    channels = db.list_channels()
    return templates.TemplateResponse(
        "channels.html",
        {
            "request": request,
            "channels": channels,
            "playlist_url": db.get_setting("playlist_url", ""),
            "tuner_count": db.get_setting("tuner_count", "2"),
            "active": "channels",
        },
    )


@app.post("/channels/refresh")
def refresh_channels(playlist_url: str = Form(...), auth=Depends(require_auth)):
    db.set_setting("playlist_url", playlist_url.strip())
    try:
        fetched = enigma_client.get_channels_playlist(playlist_url.strip())
        db.upsert_channels(fetched)
    except Exception:
        pass
    return RedirectResponse(url="/channels", status_code=303)


@app.post("/channels/save")
def save_channels(
    tuner_count: int = Form(...),
    encrypted_ids: List[int] = Form(default=[]),
    auth=Depends(require_auth),
):
    db.set_setting("tuner_count", max(1, tuner_count))
    db.set_channels_encrypted(encrypted_ids)
    return RedirectResponse(url="/channels", status_code=303)


@app.get("/konflikte")
def conflicts_page(request: Request, auth=Depends(require_auth)):
    error = None
    timers = []
    try:
        timers = enigma_client.get_timerlist()
    except Exception as exc:
        error = str(exc)

    patterns = db.list_patterns()
    channels = db.list_channels()
    tuner_count = int(db.get_setting("tuner_count", "2") or "2")
    default_priority = int(db.get_setting("default_priority", "50") or "50")

    results = conflicts.analyze(timers, patterns, channels, tuner_count, default_priority) if timers else []
    for cluster in results:
        for item in cluster["timers"]:
            item["toggle_fields"] = _timer_toggle_fields(item["raw"])

    return templates.TemplateResponse(
        "conflicts.html",
        {
            "request": request,
            "results": results,
            "error": error,
            "tuner_count": tuner_count,
            "default_priority": default_priority,
            "auto_resolve": db.get_setting("conflict_auto_resolve", "0") == "1",
            "conflict_interval": db.get_setting("conflict_check_interval_minutes", "30"),
            "last_conflict_check_at": db.get_setting("last_conflict_check_at", ""),
            "last_conflict_check_summary": db.get_setting("last_conflict_check_summary", ""),
            "next_conflict_run": scheduler.next_conflict_run_time(),
            "conflict_log": db.list_conflict_log(limit=50),
            "active": "konflikte",
        },
    )


@app.post("/konflikte/settings")
def update_conflict_settings(
    conflict_check_interval_minutes: int = Form(30),
    auto_resolve: bool = Form(False),
    auth=Depends(require_auth),
):
    db.set_setting("conflict_check_interval_minutes", max(5, conflict_check_interval_minutes))
    db.set_setting("conflict_auto_resolve", "1" if auto_resolve else "0")
    scheduler.reschedule_conflicts()
    return RedirectResponse(url="/konflikte", status_code=303)


@app.post("/konflikte/run-now")
def run_conflict_check_now(auth=Depends(require_auth)):
    scheduler.run_conflict_check_async()
    return RedirectResponse(url="/konflikte", status_code=303)
