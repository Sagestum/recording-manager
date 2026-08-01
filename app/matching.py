"""Gemeinsame Muster-Matching-Logik fuer Loesch-Regeln und Prioritaeten."""

import logging
import re

from . import db

log = logging.getLogger("matching")

DEFAULT_RULE_LABEL = "Alle Aufnahmen (Standardregel)"


def pattern_matches(pattern_row, text_value):
    text = text_value or ""
    if pattern_row["is_regex"]:
        try:
            return re.search(pattern_row["pattern"], text, re.IGNORECASE) is not None
        except re.error:
            log.warning("Ungueltiges Regex-Muster ignoriert: %s", pattern_row["pattern"])
            return False
    return pattern_row["pattern"].lower() in text.lower()


def find_matching_pattern(text_value, patterns):
    for p in patterns:
        if not p["enabled"]:
            continue
        if pattern_matches(p, text_value):
            return p
    return None


def get_default_retention_days():
    try:
        value = int(db.get_setting("default_retention_days", "0") or "0")
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def resolve_rule(eventname, patterns):
    """Ermittelt Muster-Name und Tage fuer eine Aufnahme.

    Ein spezifisches Muster hat immer Vorrang vor der globalen Standardregel,
    unabhaengig davon, ob dessen Frist kuerzer oder laenger ist.
    """
    match = find_matching_pattern(eventname, patterns)
    if match:
        return match["pattern"], match["days"]
    default_days = get_default_retention_days()
    if default_days:
        return DEFAULT_RULE_LABEL, default_days
    return None, None
