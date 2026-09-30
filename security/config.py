"""Runtime configuration, read from environment variables (or a local .env file)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path):
    """Minimal .env loader so secrets never have to live in source code."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    # Storage
    DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "faces.db"))
    FACES_DIR = os.environ.get("FACES_DIR", os.path.join(BASE_DIR, "known_faces"))
    SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", os.path.join(BASE_DIR, "snapshots"))

    # Site identity (shown on dashboard, alerts and register)
    SITE_NAME = os.environ.get("SITE_NAME", "Main Gate")
    PROJECT_NAME = os.environ.get("PROJECT_NAME", "Sangath Group")

    # Camera: integer index for USB webcam, or RTSP/HTTP URL for site CCTV/NVR
    CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "0")

    # Recognition tuning
    MATCH_TOLERANCE = _float("MATCH_TOLERANCE", 0.5)       # lower = stricter
    PROCESS_EVERY_N_FRAMES = _int("PROCESS_EVERY_N_FRAMES", 3)
    FRAME_SCALE = _float("FRAME_SCALE", 0.5)                # downscale before detection
    KNOWN_COOLDOWN_MIN = _int("KNOWN_COOLDOWN_MIN", 10)     # re-log a known person after N min
    UNKNOWN_COOLDOWN_MIN = _int("UNKNOWN_COOLDOWN_MIN", 5)  # re-alert same stranger after N min
    ON_SITE_WINDOW_MIN = _int("ON_SITE_WINDOW_MIN", 60)     # "on site now" roll-call window

    # Working hours (24h). Unknown faces outside these hours are escalated to High.
    WORK_START_HOUR = _int("WORK_START_HOUR", 8)
    WORK_END_HOUR = _int("WORK_END_HOUR", 20)

    # Telegram alerts (optional). NEVER hard-code these values.
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    # Dashboard access. If set, every route requires HTTP Basic auth.
    ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    HOST = os.environ.get("HOST", "127.0.0.1")
    PORT = _int("PORT", 5000)
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 8)


# Person categories used across enrolment, alerting and the site register.
CATEGORIES = ["Staff", "Engineer", "Contractor", "Labour", "Vendor", "Visitor", "Blacklisted"]

# Group project list (company -> projects) for tagging people to a site.
PROJECTS = [
    "Smart Life", "Pratishthan", "Mangifera", "Goodlife",  # Sangath Infrastructures Pvt. Ltd.
    "Foresta",                                               # Sangath 81 Infra Pvt. Ltd.
    "Accolade",                                              # BSB LLP
    "Head Office",
]

SEVERITIES = ["Low", "Medium", "High", "Critical"]
