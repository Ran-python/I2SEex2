"""LAN detection + network security helpers.

* ``detect_lan_ip``      - finds the lecturer machine's active LAN IP (used in the QR code).
* ``get_client_ip``      - resolves the real client IP (trusting X-Forwarded-For only from the
                           local Vite dev proxy).
* ``LanGuardMiddleware`` - secondary safeguard: rejects API requests that do not come from the
                           lecturer's machine or the same local subnet.
* ``require_lecturer_host`` - FastAPI dependency limiting lecturer actions to the host machine.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from core import config

try:  # psutil gives us the real netmask of the interface; fall back to /24 without it.
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class NetworkInfo:
    lan_ip: str
    subnet: IPNetwork
    allowed_networks: tuple[IPNetwork, ...] = field(default_factory=tuple)


def detect_lan_ip() -> str:
    """Return the IP of the interface used for outbound traffic (the active Wi-Fi/LAN adapter).

    Connecting a UDP socket does not send any packets; it only asks the OS
    which local address would be used to reach that destination.
    """
    if config.LAN_IP_OVERRIDE:
        return config.LAN_IP_OVERRIDE
    for probe in ("8.8.8.8", "1.1.1.1", "192.168.1.1", "10.255.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 80))
                ip = sock.getsockname()[0]
                if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                    return ip
        except OSError:
            continue
    if psutil is not None:
        try:
            stats = psutil.net_if_stats()
            for iface, addresses in psutil.net_if_addrs().items():
                if iface in stats and not stats[iface].isup:
                    continue
                for addr in addresses:
                    if addr.family == socket.AF_INET and addr.address:
                        parsed = _parse_ip(addr.address)
                        if parsed and not parsed.is_loopback and not parsed.is_link_local:
                            return addr.address
        except Exception:  # pragma: no cover
            pass
    return "127.0.0.1"


def _netmask_for(ip: str) -> str | None:
    if psutil is None:
        return None
    try:
        for addresses in psutil.net_if_addrs().values():
            for addr in addresses:
                if addr.family == socket.AF_INET and addr.address == ip and addr.netmask:
                    return addr.netmask
    except Exception:  # pragma: no cover - psutil platform quirks
        return None
    return None


_cache: tuple[float, NetworkInfo] | None = None
_CACHE_TTL = 30.0


def get_network_info(force_refresh: bool = False) -> NetworkInfo:
    """Cached (30s) snapshot of the host's LAN IP and subnet, refreshed if Wi-Fi changes."""
    global _cache
    now = time.monotonic()
    if not force_refresh and _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    lan_ip = detect_lan_ip()
    netmask = _netmask_for(lan_ip) or ("255.0.0.0" if lan_ip.startswith("127.") else "255.255.255.0")
    subnet = ipaddress.ip_network(f"{lan_ip}/{netmask}", strict=False)

    allowed: list[IPNetwork] = [subnet]
    for cidr in config.EXTRA_ALLOWED_SUBNETS:
        try:
            allowed.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass

    info = NetworkInfo(lan_ip=lan_ip, subnet=subnet, allowed_networks=tuple(allowed))
    _cache = (now, info)
    return info


def _parse_ip(raw: str | None) -> IPAddress | None:
    if not raw:
        return None
    raw = raw.strip().strip("[]")
    if raw.lower().startswith("::ffff:"):
        raw = raw[7:]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def get_client_ip(request: Request) -> str:
    """Real client IP.

    The Vite dev server proxies ``/api`` from the same machine and appends the
    browser's address to ``X-Forwarded-For``. We only trust that header when
    the direct peer is loopback (our own proxy), and we take the *last* entry
    (the one written by our proxy), so a client can't spoof it.
    """
    peer = _parse_ip(request.client.host if request.client else None)
    if peer is not None and peer.is_loopback:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            last = _parse_ip(forwarded.split(",")[-1])
            if last is not None:
                return str(last)
    return str(peer) if peer is not None else "0.0.0.0"


def is_host_ip(ip_str: str) -> bool:
    """True when the request comes from the lecturer's own computer."""
    ip = _parse_ip(ip_str)
    if ip is None:
        return False
    return ip.is_loopback or str(ip) == get_network_info().lan_ip


def is_lan_ip_allowed(ip_str: str) -> bool:
    ip = _parse_ip(ip_str)
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    return any(ip.version == net.version and ip in net for net in get_network_info().allowed_networks)


class LanGuardMiddleware(BaseHTTPMiddleware):
    """Rejects ``/api`` requests that originate outside the lecturer's local subnet."""

    async def dispatch(self, request: Request, call_next):
        if config.DISABLE_LAN_GUARD or not request.url.path.startswith("/api"):
            return await call_next(request)
        client_ip = get_client_ip(request)
        if not is_lan_ip_allowed(client_ip):
            info = get_network_info()
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Access denied: you must be connected to the same Wi-Fi network as the "
                        f"lecturer (expected subnet {info.subnet}, your IP {client_ip})."
                    )
                },
            )
        request.state.client_ip = client_ip
        return await call_next(request)


def require_lecturer_host(request: Request) -> None:
    """Dependency: lecturer actions are accepted only from the lecturer's computer."""
    if not config.LECTURER_HOST_ONLY:
        return
    if not is_host_ip(get_client_ip(request)):
        raise HTTPException(
            status_code=403,
            detail="Lecturer actions are only available on the lecturer's computer.",
        )
