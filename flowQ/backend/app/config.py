import os
import secrets
import warnings
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))
    jwt_secret: str = os.getenv("JWT_SECRET", "").strip()
    jwt_expire_hours: int = int(os.getenv("JWT_EXPIRE_HOURS", "12"))
    app_timezone: str = os.getenv("APP_TIMEZONE", "Asia/Karachi").strip()
    # When true, walk-in tokens can only be taken between a department's opening and closing time.
    enforce_working_hours: bool = _bool("ENFORCE_WORKING_HOURS", "false")
    # A called customer who is skipped this many times is marked as a no-show.
    max_skips: int = int(os.getenv("MAX_SKIPS", "3"))
    cors_origins: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    frontend_dir: Path = Path(os.getenv("FRONTEND_DIR", str(PROJECT_DIR / "frontend")))


settings = Settings()

if not settings.jwt_secret:
    warnings.warn("JWT_SECRET is not set; using a random secret. Everyone is logged out on restart.")
    settings.jwt_secret = secrets.token_urlsafe(48)
