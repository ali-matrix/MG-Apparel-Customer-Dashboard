"""Central configuration. Everything comes from environment variables (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> project root is two levels above "app"
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")  # real environment variables win over .env


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}")


ERP_BASE_URL = os.getenv("ERP_BASE_URL", "").rstrip("/")
ERP_API_KEY = os.getenv("ERP_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")

_db = Path(os.getenv("DB_PATH", "backend/portal.duckdb"))
DB_PATH = _db if _db.is_absolute() else ROOT / _db

SYNC_INTERVAL_MIN = _int("SYNC_INTERVAL_MIN", 15)

# ERP client behaviour
ERP_PAGE_SIZE = 50        # API maximum, fewest calls possible
ERP_TIMEOUT_S = 15
ERP_MAX_ATTEMPTS = 4      # per request (1 try + 3 retries)
ERP_MAX_PAGES = 1000      # safety stop against endless pagination

SEED_PASSWORD = os.getenv("SEED_PASSWORD", "Portal@2026")
JWT_EXPIRE_MIN = _int("JWT_EXPIRE_MIN", 60)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")