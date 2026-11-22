"""Central configuration for the teaching platform backend.

All values can be overridden with environment variables so the lecturer can
adapt the demo to a specific classroom network without touching the code.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


BASE_DIR = Path(__file__).resolve().parent.parent          # ./backend
PROJECT_DIR = BASE_DIR.parent                               # ./
FRONTEND_DIST_DIR = PROJECT_DIR / "frontend" / "dist"

# --- Server ---------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")                       # bind to every interface -> reachable over LAN
PORT = int(os.getenv("PORT", "8000"))
DEFAULT_FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))

# --- Database -------------------------------------------------------------
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "attendance.db")))
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

# --- Attendance rules -----------------------------------------------------
SESSION_DURATION_SECONDS = int(os.getenv("SESSION_DURATION_SECONDS", str(15 * 60)))
DEVICE_LOCK_SECONDS = int(os.getenv("DEVICE_LOCK_SECONDS", str(15 * 60)))
DEVICE_LOCK_COOKIE = "device_lock_token"

# When True (default), a matching browser/device fingerprint locks the device for
# 15 minutes even if cookies/localStorage are cleared or incognito is used.
STRICT_FINGERPRINT_LOCK = _env_bool("STRICT_FINGERPRINT_LOCK", True)

# --- Network security -----------------------------------------------------
# Override the auto-detected LAN IP (useful with multiple adapters / VPNs).
LAN_IP_OVERRIDE = os.getenv("LAN_IP") or None
# Extra CIDR ranges that are allowed to reach the API, comma separated.
EXTRA_ALLOWED_SUBNETS = [s.strip() for s in os.getenv("ALLOWED_SUBNETS", "").split(",") if s.strip()]
# Lecturer-only endpoints (open/close session, import, export...) are accepted
# only from the lecturer's own computer.
LECTURER_HOST_ONLY = _env_bool("LECTURER_HOST_ONLY", True)
# Disable the subnet guard entirely (NOT recommended, debugging only).
DISABLE_LAN_GUARD = _env_bool("DISABLE_LAN_GUARD", False)
