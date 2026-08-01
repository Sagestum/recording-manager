import logging
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, enigma_client, scheduler
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
        rows.append(
            {
                "eventname": eventname,
                "servicename": m.get("servicename", ""),
                "recordingtime": m.get("recordingtime"),
                "filesize_readable": m.get("filesize_readable", ""),
                "length": m.get("length", ""),
                "serviceref": m.get("serviceref") or m.get("fullname"),
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


@app.get("/timers")
def timers_page(request: Request, auth=Depends(require_auth)):
    error = None
    timers = []
    try:
        timers = enigma_client.get_timerlist()
    except Exception as exc:
        error = str(exc)

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
        rows.append(
            {
                "name": t.get("name", ""),
                "servicename": t.get("servicename", ""),
                "begin": begin,
                "end": end,
                "status": label,
            }
        )
    rows.sort(key=lambda r: r["begin"])

    return templates.TemplateResponse(
        "timers.html", {"request": request, "rows": rows, "error": error, "active": "timers"}
    )


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
    auth=Depends(require_auth),
):
    if pattern.strip():
        db.add_pattern(pattern, days, is_regex, True)
    return RedirectResponse(url="/patterns", status_code=303)


@app.post("/patterns/{pattern_id}/update")
def update_pattern_route(
    pattern_id: int,
    pattern: str = Form(...),
    days: int = Form(...),
    is_regex: bool = Form(False),
    enabled: bool = Form(False),
    auth=Depends(require_auth),
):
    db.update_pattern(pattern_id, pattern, days, is_regex, enabled)
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
    auth=Depends(require_auth),
):
    db.set_setting("check_interval_hours", max(1, interval_hours))
    db.set_setting("default_retention_days", max(0, default_retention_days))
    scheduler.reschedule()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/run-now")
def run_now(auth=Depends(require_auth)):
    scheduler.run_check_async()
    return RedirectResponse(url="/settings", status_code=303)
