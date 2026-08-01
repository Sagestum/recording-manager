import logging
import re
from xml.etree import ElementTree

import requests
from requests.auth import HTTPBasicAuth

from .config import ENIGMA_BASE_URL, ENIGMA_PASS, ENIGMA_USER

log = logging.getLogger("enigma_client")

_M3U_ENTRY_RE = re.compile(
    r'tvg-id="(?P<ref>[^"]*)"\s+tvg-name="(?P<name>[^"]*)"'
)


def _auth():
    if ENIGMA_USER:
        return HTTPBasicAuth(ENIGMA_USER, ENIGMA_PASS)
    return None


def _get(path, params=None, timeout=10):
    url = f"{ENIGMA_BASE_URL}{path}"
    resp = requests.get(url, params=params, auth=_auth(), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_movielist():
    """Abgeschlossene Aufnahmen auf der Festplatte."""
    data = _get("/api/movielist")
    return data.get("movies", [])


def get_timerlist():
    """Geplante / laufende Timer (inkl. AutoTimer-generierte)."""
    data = _get("/api/timerlist")
    return data.get("timers", [])


def delete_movie(service_ref):
    data = _get("/api/moviedelete", params={"sRef": service_ref})
    return bool(data.get("result")), data.get("message", "")


def get_status():
    return _get("/api/statusinfo")


def set_timer_disabled(timer, disabled):
    """timer: dict mit mind. serviceref, begin, end (aus /api/timerlist).
    Nutzt exakt die Parameter, die OpenWebif fuer /api/timerchange erwartet.
    """
    params = {
        "sRef": timer.get("serviceref", ""),
        "channelOld": timer.get("serviceref", ""),
        "beginOld": timer.get("begin", 0),
        "endOld": timer.get("end", 0),
        "begin": timer.get("begin", 0),
        "end": timer.get("end", 0),
        "name": timer.get("name", ""),
        "description": timer.get("description", ""),
        "disabled": int(bool(disabled)),
        "eit": timer.get("eit", 0),
        "justplay": timer.get("justplay", 0),
        "repeated": timer.get("repeated", 0),
    }
    data = _get("/api/timerchange", params=params)
    return bool(data.get("result")), data.get("message", "")


def get_autotimers():
    """Liest die Regeln des AutoTimer-Plugins direkt aus dessen XML-Endpunkt
    (nicht Teil der regulaeren OpenWebif-/api/-JSON-API)."""
    url = f"{ENIGMA_BASE_URL}/autotimer"
    resp = requests.get(url, auth=_auth(), timeout=15)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.text)
    entries = []
    for timer_el in root.findall("timer"):
        name = (timer_el.get("name") or "").strip()
        match = (timer_el.get("match") or name).strip()
        if not match:
            continue
        entries.append(
            {
                "name": name,
                "match": match,
                "enabled": timer_el.get("enabled", "yes") == "yes",
            }
        )
    return entries


def get_channels_playlist(url_or_path):
    url = url_or_path if url_or_path.startswith("http") else f"{ENIGMA_BASE_URL}{url_or_path}"
    resp = requests.get(url, auth=_auth(), timeout=15)
    resp.raise_for_status()
    channels = []
    seen = set()
    for match in _M3U_ENTRY_RE.finditer(resp.text):
        ref = match.group("ref").strip()
        name = match.group("name").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        channels.append({"service_ref": ref, "name": name})
    return channels
