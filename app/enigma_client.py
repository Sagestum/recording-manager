import logging

import requests
from requests.auth import HTTPBasicAuth

from .config import ENIGMA_BASE_URL, ENIGMA_PASS, ENIGMA_USER

log = logging.getLogger("enigma_client")


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
