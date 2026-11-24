"""Authentication & URL resolution helpers."""
from __future__ import annotations

from fastapi import HTTPException, Request

from core import config


def is_https_request(request: Request | None) -> bool:
    """True when the request arrived over HTTPS (directly or via a reverse proxy)."""
    if request is None:
        return False
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    return request.url.scheme.lower() == "https"


def resolve_app_base_url(
    request: Request | None = None,
    base_url: str | None = None,
    frontend_port: int | None = None,
) -> str:
    """Determine the routable origin URL to encode into QR codes."""
    if config.PUBLIC_APP_URL:
        return config.PUBLIC_APP_URL.rstrip("/")

    if base_url and base_url.strip():
        cleaned = base_url.strip().rstrip("/")
        if cleaned.startswith(("http://", "https://")):
            return cleaned

    if request is not None:
        origin = (request.headers.get("origin") or "").strip().rstrip("/")
        if origin.startswith(("http://", "https://")):
            return origin

        scheme = "https" if is_https_request(request) else (request.url.scheme or "http")
        fwd_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        host = fwd_host or (request.headers.get("host") or "").strip() or request.url.netloc
        if host:
            if frontend_port and host.endswith(f":{config.PORT}") and frontend_port != config.PORT:
                host = host[: -len(f":{config.PORT}")] + ("" if frontend_port in (80, 443) else f":{frontend_port}")
            return f"{scheme}://{host}".rstrip("/")

    port = frontend_port or config.DEFAULT_FRONTEND_PORT
    port_part = "" if port in (80, 443) else f":{port}"
    return f"http://localhost{port_part}"


def require_lecturer_host(request: Request = None) -> None:
    """Verify lecturer PIN if provided in headers."""
    if request is None:
        return None
    pin = (request.headers.get("x-lecturer-pin") or request.query_params.get("pin") or "").strip()
    if pin and pin != config.LECTURER_PIN:
        raise HTTPException(
            status_code=403,
            detail="Invalid Lecturer PIN (access code 3358 required).",
        )
    return None


def require_lecturer_pin(request: Request) -> None:
    """Strict lecturer PIN dependency for lecturer-only actions."""
    pin = (request.headers.get("x-lecturer-pin") or request.query_params.get("pin") or "").strip()
    if pin != config.LECTURER_PIN:
        raise HTTPException(
            status_code=403,
            detail="Lecturer access required. Please unlock Lecturer Mode with PIN 3358.",
        )
    return None
