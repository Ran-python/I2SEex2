"""Central configuration for the teaching platform backend.

All values can be overridden with environment variables so the lecturer can
adapt the demo to a specific classroom network without touching the code.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent          # ./backend
PROJECT_DIR = BASE_DIR.parent                               # ./
FRONTEND_DIST_DIR = PROJECT_DIR / "frontend" / "dist"

# Load environment variables from backend/.env*, ./.env.local, and ./.env
for _env_file in (
    BASE_DIR / ".env.local",
    BASE_DIR / ".env",
    PROJECT_DIR / ".env.local",
    PROJECT_DIR / ".env",
):
    if _env_file.is_file():
        load_dotenv(_env_file, override=False)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(raw_url: str | None) -> str:
    """Normalize a PostgreSQL or SQLite URL for SQLAlchemy 2.x + psycopg v3."""
    if not raw_url:
        return f"sqlite:///{DATABASE_PATH.as_posix()}"
    url = raw_url.strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def summarize_database_url(url: str) -> str:
    if url.startswith("sqlite"):
        return f"SQLite ({DATABASE_PATH})"
    try:
        parts = urlsplit(url)
        host = parts.hostname or "postgres"
        db_name = (parts.path or "").lstrip("/") or "postgres"
        return f"Neon PostgreSQL ({host}/{db_name})"
    except Exception:
        return "Neon PostgreSQL"


# --- Server ---------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")                       # bind to every interface -> reachable over LAN
PORT = int(os.getenv("PORT", "8000"))
DEFAULT_FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))

# --- Database -------------------------------------------------------------
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "attendance.db")))
RAW_DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or os.getenv("NEON_DATABASE_URL")
DATABASE_URL = normalize_database_url(RAW_DATABASE_URL)
IS_POSTGRES = DATABASE_URL.startswith("postgresql")
DATABASE_SUMMARY = summarize_database_url(DATABASE_URL)

# --- Attendance rules -----------------------------------------------------
SESSION_DURATION_SECONDS = int(os.getenv("SESSION_DURATION_SECONDS", str(15 * 60)))
DEVICE_LOCK_SECONDS = int(os.getenv("DEVICE_LOCK_SECONDS", str(15 * 60)))
DEVICE_LOCK_COOKIE = "device_lock_token"

# When True (default), a matching browser/device fingerprint locks the device for
# 15 minutes even if cookies/localStorage are cleared or incognito is used.
STRICT_FINGERPRINT_LOCK = _env_bool("STRICT_FINGERPRINT_LOCK", True)

# Lecturer PIN for role access control (default: 3358)
LECTURER_PIN = os.getenv("LECTURER_PIN", "3358").strip() or "3358"

# Optional fallback AI keys from environment
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip() or None
ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip() or None

# Optional public base URL for QR codes (e.g. https://my-app.onrender.com).
# When unset, the backend uses the origin URL passed by the lecturer's browser or Host headers.
PUBLIC_APP_URL = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/") or None

# --- MP4 Audio Preprocessing & Transcription Pipeline ---------------------
FFMPEG_BIN = (os.getenv("FFMPEG_BIN") or "").strip() or None
FFPROBE_BIN = (os.getenv("FFPROBE_BIN") or "").strip() or None

# Maximum uploaded media file size in MB (streamed to disk, never loaded into RAM)
MAX_VIDEO_UPLOAD_MB = float(os.getenv("MAX_VIDEO_UPLOAD_MB", "1024"))
MAX_VIDEO_UPLOAD_BYTES = int(MAX_VIDEO_UPLOAD_MB * 1024 * 1024)
MAX_MEDIA_DURATION_SECONDS = float(os.getenv("MAX_MEDIA_DURATION_SECONDS", str(5 * 3600)))

# Audio preprocessing defaults (mono, 16 kHz, speech-optimized MP3)
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_CHANNELS = int(os.getenv("AUDIO_CHANNELS", "1"))
AUDIO_BITRATE = (os.getenv("AUDIO_BITRATE") or "48k").strip()

# Transcription upload size limit (OpenAI hard limit is 25 MB; default 20 MB leaves a 5 MB safety margin)
OPENAI_HARD_LIMIT_MB = 25.0
STT_MAX_UPLOAD_MB = float(os.getenv("STT_MAX_UPLOAD_MB", "20.0"))
STT_MAX_UPLOAD_BYTES = int(STT_MAX_UPLOAD_MB * 1024 * 1024)

# Chunking & overlap configuration
AUDIO_CHUNK_TARGET_SECONDS = float(os.getenv("AUDIO_CHUNK_TARGET_SECONDS", "600.0"))
AUDIO_CHUNK_OVERLAP_SECONDS = float(os.getenv("AUDIO_CHUNK_OVERLAP_SECONDS", "1.5"))
TRANSCRIPTION_MAX_RETRIES = int(os.getenv("TRANSCRIPTION_MAX_RETRIES", "3"))
TRANSCRIPTION_DEFAULT_LANGUAGE = (os.getenv("TRANSCRIPTION_DEFAULT_LANGUAGE") or "").strip().lower() or None
OPENAI_STT_MODEL = (os.getenv("OPENAI_STT_MODEL") or "whisper-1").strip()
STT_PROVIDER_OVERRIDE = (os.getenv("STT_PROVIDER") or "").strip().lower() or None
SUMMARY_MAX_WORKERS = int(os.getenv("SUMMARY_MAX_WORKERS", "3"))
