import os

ENIGMA_SCHEME = os.environ.get("ENIGMA_SCHEME", "http")
ENIGMA_HOST = os.environ.get("ENIGMA_HOST", "192.168.0.5")
ENIGMA_PORT = os.environ.get("ENIGMA_PORT", "80")
ENIGMA_USER = os.environ.get("ENIGMA_USER", "")
ENIGMA_PASS = os.environ.get("ENIGMA_PASS", "")

ENIGMA_BASE_URL = f"{ENIGMA_SCHEME}://{ENIGMA_HOST}:{ENIGMA_PORT}"

DB_PATH = os.environ.get("DB_PATH", "/data/app.db")
DEFAULT_CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", "6"))

MANAGER_USER = os.environ.get("MANAGER_USER", "")
MANAGER_PASS = os.environ.get("MANAGER_PASS", "")

TZ_NAME = os.environ.get("TZ", "Europe/Berlin")
