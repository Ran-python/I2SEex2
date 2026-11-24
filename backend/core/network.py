"""Network, Public IP, and ISP/ASN verification helpers (University Wi-Fi + Cloudflare Tunnel + Render compatible)."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from core import config

try:
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


def _parse_ip(raw: str | None) -> IPAddress | None:
    if not raw:
        return None
    raw = raw.strip().strip("[]").strip('"')
    # Strip optional port if present on an IPv4 address (e.g. "147.235.210.5:54321")
    if raw.count(":") == 1 and "." in raw:
        raw = raw.split(":", 1)[0]
    if raw.lower().startswith("::ffff:"):
        raw = raw[7:]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def normalize_ip(raw: str | None) -> str:
    parsed = _parse_ip(raw)
    return str(parsed) if parsed is not None else "127.0.0.1"


def is_loopback_ip(ip_str: str | None) -> bool:
    ip = _parse_ip(ip_str)
    return bool(ip and ip.is_loopback)


def is_private_or_loopback(ip_str: str | None) -> bool:
    ip = _parse_ip(ip_str)
    if ip is None:
        return True
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def get_client_ip(request: Request | None) -> str:
    """Extract the client's real IP address behind reverse proxies (Cloudflare Tunnel, Vite, Render, ngrok).

    Guaranteed NEVER to return 127.0.0.1 when a non-loopback IP is present in
    CF-Connecting-IP, True-Client-IP, X-Forwarded-For, or X-Real-IP.
    """
    if request is None:
        return "127.0.0.1"

    candidates: list[IPAddress] = []

    for header in ("cf-connecting-ip", "true-client-ip"):
        val = request.headers.get(header)
        if val:
            parsed = _parse_ip(val)
            if parsed is not None:
                if not parsed.is_loopback:
                    return str(parsed)
                candidates.append(parsed)

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        for part in forwarded.split(","):
            parsed = _parse_ip(part)
            if parsed is not None:
                if not parsed.is_loopback:
                    return str(parsed)
                candidates.append(parsed)

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        parsed = _parse_ip(real_ip)
        if parsed is not None:
            if not parsed.is_loopback:
                return str(parsed)
            candidates.append(parsed)

    peer = _parse_ip(request.client.host if request.client else None)
    if peer is not None:
        if not peer.is_loopback:
            return str(peer)
        candidates.append(peer)

    return str(candidates[0]) if candidates else "127.0.0.1"


def compute_subnet(ip_str: str | None) -> str:
    """Return the /24 subnet for an IPv4 address (or /64 for IPv6)."""
    ip = _parse_ip(ip_str)
    if ip is None or ip.is_loopback:
        return "127.0.0.0/24"
    prefix = 24 if isinstance(ip, ipaddress.IPv4Address) else 64
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


# ---------------------------------------------------------------------------
# Public IP & Multi-Source ASN / ISP Resolution (Cached)
# ---------------------------------------------------------------------------
_PUBLIC_IP_CACHE: tuple[float, str] | None = None
_PUBLIC_IP_TTL = 300.0
_ASN_CACHE: dict[str, tuple[float, dict]] = {}
_ASN_CACHE_TTL = 1800.0


def normalize_asn(raw_asn: str | int | None) -> str | None:
    """Normalize an ASN string like 'AS12849 Partner Communications' or 'AS12849' or 12849 to 'AS12849'."""
    if raw_asn is None:
        return None
    text = str(raw_asn).strip().upper()
    if not text:
        return None
    m = re.search(r"(?:AS)?(\d{1,10})", text)
    if m:
        return f"AS{m.group(1)}"
    return None


def detect_host_public_ip(force_refresh: bool = False) -> str | None:
    """Detect the server/router's outbound Public IPv4 address (cached for 5 minutes).

    Used when the lecturer opens a session from localhost / LAN (`127.0.0.1` / `192.168.x.x`)
    while students scan the QR code via Cloudflare Tunnel (which arrives with the router's Public IP).
    """
    global _PUBLIC_IP_CACHE
    now = time.monotonic()
    if not force_refresh and _PUBLIC_IP_CACHE and (now - _PUBLIC_IP_CACHE[0] < _PUBLIC_IP_TTL):
        return _PUBLIC_IP_CACHE[1]

    endpoints = (
        "https://api.ipify.org?format=json",
        "https://ipwho.is/",
        "https://ipapi.co/json/",
        "http://ip-api.com/json/",
    )
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ClassPulse-NetworkAudit/2.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                candidate = data.get("ip") or data.get("query")
                parsed = _parse_ip(candidate)
                if parsed and not parsed.is_loopback and not parsed.is_private:
                    ip_str = str(parsed)
                    _PUBLIC_IP_CACHE = (now, ip_str)
                    return ip_str
        except Exception:
            continue
    return _PUBLIC_IP_CACHE[1] if _PUBLIC_IP_CACHE else None


def lookup_ip_asn(ip_str: str | None) -> dict:
    """Look up the ASN and ISP/Org name for a public IPv4 or IPv6 address.

    Uses a multi-source fallback chain (ipwho.is -> ipapi.co -> ip-api.com) and caches results.
    Returns: {"ip": str, "asn": "ASxxxx" | None, "org": str | None}
    """
    ip = _parse_ip(ip_str)
    if ip is None:
        return {"ip": str(ip_str or ""), "asn": None, "org": "Invalid IP"}
    norm = str(ip)
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return {"ip": norm, "asn": "LOCAL", "org": "Local Classroom Network (LAN / Loopback)"}

    now = time.monotonic()
    if norm in _ASN_CACHE:
        ts, cached_val = _ASN_CACHE[norm]
        if now - ts < _ASN_CACHE_TTL:
            return cached_val

    # Source 1: ipwho.is (HTTPS, no rate key required, supports IPv4 & IPv6)
    try:
        url = f"https://ipwho.is/{norm}"
        req = urllib.request.Request(url, headers={"User-Agent": "ClassPulse/2.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("success") is not False:
                conn = data.get("connection") or {}
                asn = normalize_asn(conn.get("asn"))
                org = conn.get("isp") or conn.get("org")
                if asn or org:
                    res = {"ip": norm, "asn": asn, "org": org or asn}
                    _ASN_CACHE[norm] = (now, res)
                    return res
    except Exception:
        pass

    # Source 2: ipapi.co (HTTPS, supports IPv4 & IPv6)
    try:
        url = f"https://ipapi.co/{norm}/json/"
        req = urllib.request.Request(url, headers={"User-Agent": "ClassPulse/2.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            asn = normalize_asn(data.get("asn"))
            org = data.get("org")
            if asn or org:
                res = {"ip": norm, "asn": asn, "org": org or asn}
                _ASN_CACHE[norm] = (now, res)
                return res
    except Exception:
        pass

    # Source 3: ip-api.com
    try:
        url = f"http://ip-api.com/json/{norm}?fields=status,as,isp,org"
        req = urllib.request.Request(url, headers={"User-Agent": "ClassPulse/2.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if data.get("status") == "success":
                asn = normalize_asn(data.get("as"))
                org = data.get("isp") or data.get("org") or data.get("as")
                if asn or org:
                    res = {"ip": norm, "asn": asn, "org": org or asn}
                    _ASN_CACHE[norm] = (now, res)
                    return res
    except Exception:
        pass

    res = {"ip": norm, "asn": None, "org": None}
    _ASN_CACHE[norm] = (now, res)
    return res


def parse_network_profile(lecturer_ip: str | None, lecturer_subnet: str | None) -> dict:
    """Decode stored lecturer network metadata (supports pipe-encoded public IP & ASN in lecturer_subnet)."""
    raw_sub = (lecturer_subnet or "").strip()
    parts = [p.strip() for p in raw_sub.split("|") if p.strip()]
    primary_subnet = parts[0] if parts else compute_subnet(lecturer_ip)
    public_ip: str | None = None
    public_subnet: str | None = None
    asn: str | None = None
    org: str | None = None
    allow_any = False

    for token in parts[1:]:
        if token.startswith("pub="):
            public_ip = token[4:].strip() or None
            if public_ip:
                public_subnet = compute_subnet(public_ip)
        elif token.startswith("asn="):
            asn = normalize_asn(token[4:].strip())
        elif token.startswith("org="):
            org = token[4:].strip() or None
        elif token == "override=1":
            allow_any = True

    if not public_ip and lecturer_ip and not is_private_or_loopback(lecturer_ip):
        public_ip = normalize_ip(lecturer_ip)
        public_subnet = compute_subnet(public_ip)

    return {
        "lecturer_ip": normalize_ip(lecturer_ip) if lecturer_ip else "127.0.0.1",
        "primary_subnet": primary_subnet,
        "public_ip": public_ip,
        "public_subnet": public_subnet,
        "asn": asn,
        "org": org,
        "allow_any_network": allow_any,
    }


def build_lecturer_network_lock(lecturer_ip: str | None, allow_any_network: bool = False) -> tuple[str, str]:
    """Capture both the lecturer's detected IP and the classroom router's Public IP + ISP ASN.

    Returns ``(effective_lecturer_ip, encoded_lecturer_subnet)`` ready to store in ``Lesson``
    columns (fitting cleanly inside ``VARCHAR(64)`` or ``VARCHAR(255)``).
    """
    norm_ip = normalize_ip(lecturer_ip)
    public_ip: str | None = None
    if not is_private_or_loopback(norm_ip):
        public_ip = norm_ip
    else:
        public_ip = detect_host_public_ip()

    # If the lecturer opened the page via localhost/LAN, prefer storing the public IP when available,
    # or keep norm_ip and embed pub=<public_ip> + asn=<ASN> in the subnet spec.
    effective_ip = public_ip or norm_ip
    primary_subnet = compute_subnet(effective_ip)

    asn_info = lookup_ip_asn(public_ip) if public_ip else {"asn": None, "org": None}
    asn = asn_info.get("asn")
    tokens = [primary_subnet]
    if public_ip and public_ip != norm_ip:
        tokens.append(f"pub={public_ip}")
    if asn and asn != "LOCAL":
        tokens.append(f"asn={asn}")
    if allow_any_network:
        tokens.append("override=1")

    encoded = "|".join(tokens)
    return effective_ip, encoded[:64]


def verify_classroom_network(
    student_ip: str | None,
    lecturer_ip: str | None,
    lecturer_subnet: str | None,
) -> tuple[bool, str, dict]:
    """Methodically verify whether ``student_ip`` is on the same classroom Wi-Fi as the lecturer.

    Handles all 4 real-world classroom topologies:
      1. Demo Override / LAN Guard Disabled -> allowed.
      2. Direct LAN / Localhost (student and lecturer both on localhost or same private LAN /24).
      3. Same Public IPv4 or /24 NAT pool (e.g. lecturer opened via localhost or Cloudflare Tunnel,
         student scanned Cloudflare Tunnel QR on the same Wi-Fi router).
      4. Dual-Stack IPv4 vs IPv6 on the same Wi-Fi router (lecturer on IPv4 ``84.108.x.x``,
         student iPhone/Android on IPv6 ``2a06:...`` sharing the exact same ISP ASN).
    """
    profile = parse_network_profile(lecturer_ip, lecturer_subnet)
    s_ip = _parse_ip(student_ip)
    s_norm = str(s_ip) if s_ip else str(student_ip or "")
    s_asn_info = lookup_ip_asn(s_norm) if (s_ip and not is_private_or_loopback(s_norm)) else {"asn": "LOCAL", "org": "Local LAN"}

    diag = {
        "student_ip": s_norm,
        "student_subnet": compute_subnet(s_norm),
        "student_asn": s_asn_info.get("asn"),
        "student_org": s_asn_info.get("org"),
        "lecturer_ip": profile["lecturer_ip"],
        "lecturer_public_ip": profile["public_ip"],
        "lecturer_subnet": profile["primary_subnet"],
        "lecturer_asn": profile["asn"],
        "allow_any_network": profile["allow_any_network"],
    }

    if config.DISABLE_LAN_GUARD or profile["allow_any_network"]:
        return True, "Allowed (Demo Network Override active)", diag

    if not lecturer_ip and not lecturer_subnet:
        return True, "Allowed (No session network lock recorded)", diag

    if s_ip is None:
        return False, f"Invalid client IP address ({student_ip})", diag

    l_ip = _parse_ip(profile["lecturer_ip"])
    pub_ip = _parse_ip(profile["public_ip"]) if profile["public_ip"] else None

    # If the lecturer opened the session from localhost before public IP was cached, resolve host public IP dynamically
    if pub_ip is None and (l_ip is None or is_private_or_loopback(str(l_ip))):
        live_pub = detect_host_public_ip()
        if live_pub:
            pub_ip = _parse_ip(live_pub)
            diag["lecturer_public_ip"] = live_pub

    # 1. Exact IP match (same NAT gateway or same machine)
    if (l_ip and s_ip == l_ip) or (pub_ip and s_ip == pub_ip):
        return True, "Exact classroom IP match", diag

    # 2. Localhost / Loopback or Local LAN match
    if s_ip.is_loopback and (l_ip is None or is_private_or_loopback(str(l_ip))):
        return True, "Localhost loopback match", diag

    local_lan = _parse_ip(get_network_info().lan_ip)
    if s_ip.is_private and local_lan and s_ip.version == local_lan.version:
        lan_net = ipaddress.ip_network(compute_subnet(str(local_lan)), strict=False)
        if s_ip in lan_net:
            return True, "Same local Wi-Fi LAN /24 subnet", diag

    # 3. Same Public /24 (IPv4) or /64 (IPv6) Subnet
    candidate_subnets = [profile["primary_subnet"], profile["public_subnet"]]
    if pub_ip:
        candidate_subnets.append(compute_subnet(str(pub_ip)))
    if l_ip and not l_ip.is_loopback:
        candidate_subnets.append(compute_subnet(str(l_ip)))
    candidate_subnets.extend(config.EXTRA_ALLOWED_SUBNETS)

    for cidr in candidate_subnets:
        if not cidr:
            continue
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            if s_ip.version == net.version and s_ip in net:
                return True, f"Matched classroom subnet {net}", diag
        except ValueError:
            continue

    # 4. Dual-Stack IPv4 <-> IPv6 on same Wi-Fi ISP (ASN Verification)
    # When a phone on Wi-Fi connects to Cloudflare Tunnel over IPv6 while the laptop is on IPv4
    # (or vice-versa, or CGNAT pools on the same ISP), verify their Autonomous System Number (ASN).
    lecturer_asn = profile["asn"]
    if not lecturer_asn and pub_ip:
        l_asn_info = lookup_ip_asn(str(pub_ip))
        lecturer_asn = l_asn_info.get("asn")
        diag["lecturer_asn"] = lecturer_asn
        diag["lecturer_org"] = l_asn_info.get("org")

    student_asn = s_asn_info.get("asn")
    if (
        lecturer_asn
        and student_asn
        and lecturer_asn != "LOCAL"
        and student_asn != "LOCAL"
        and normalize_asn(lecturer_asn) == normalize_asn(student_asn)
    ):
        # If both are IPv4 and in completely different /16s, only allow ASN match when versions differ (IPv4 vs IPv6)
        # OR when both share the same university/ISP ASN.
        if (l_ip and s_ip.version != l_ip.version) or (pub_ip and s_ip.version != pub_ip.version):
            return True, f"Dual-stack IPv4/IPv6 match on same Wi-Fi ISP ({lecturer_asn})", diag

    expected_desc = f"{profile['primary_subnet']}"
    if diag.get("lecturer_public_ip"):
        expected_desc += f" (Public IP: {diag['lecturer_public_ip']}"
        if lecturer_asn:
            expected_desc += f", {lecturer_asn}"
        expected_desc += ")"
    elif lecturer_asn:
        expected_desc += f" ({lecturer_asn})"

    student_desc = f"{s_norm}"
    if student_asn:
        student_desc += f" ({student_asn}"
        if s_asn_info.get("org"):
            student_desc += f" - {s_asn_info['org']}"
        student_desc += ")"

    reason = (
        "Check-in blocked: Your device does not appear to be on the classroom Wi-Fi network. "
        f"Expected classroom network: {expected_desc} | Your detected IP: {student_desc}."
    )
    return False, reason, diag


def is_same_classroom_net(
    student_ip: str | None,
    lecturer_ip: str | None,
    lecturer_subnet: str | None,
) -> bool:
    ok, _reason, _diag = verify_classroom_network(student_ip, lecturer_ip, lecturer_subnet)
    return ok


def is_https_request(request: Request | None) -> bool:
    """True when the request arrived over HTTPS (directly or via a reverse proxy like Render)."""
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
    """Determine the routable origin URL to encode into the QR code.

    Priority:
      1. ``PUBLIC_APP_URL`` environment variable (if set)
      2. ``base_url`` passed from ``window.location.origin`` in the lecturer's browser
      3. ``Origin`` / ``X-Forwarded-Host`` / ``Host`` headers on the incoming request
      4. ``http://localhost:<frontend_port>`` fallback
    """
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
            # If the request hit port 8000 directly in local Vite dev mode and a custom
            # frontend_port (e.g. 5173) was provided, rewrite the port to frontend_port.
            if frontend_port and host.endswith(f":{config.PORT}") and frontend_port != config.PORT:
                host = host[: -len(f":{config.PORT}")] + ("" if frontend_port in (80, 443) else f":{frontend_port}")
            return f"{scheme}://{host}".rstrip("/")

    port = frontend_port or config.DEFAULT_FRONTEND_PORT
    port_part = "" if port in (80, 443) else f":{port}"
    return f"http://localhost{port_part}"


def detect_lan_ip() -> str:
    """Detect the local adapter IP (kept for local network diagnostics)."""
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


_cache: tuple[float, NetworkInfo] | None = None
_CACHE_TTL = 30.0


def get_network_info(force_refresh: bool = False) -> NetworkInfo:
    """Snapshot of the local machine's IP and /24 subnet."""
    global _cache
    now = time.monotonic()
    if not force_refresh and _cache and now - _cache[0] < _CACHE_TTL:
        return _cache[1]

    lan_ip = detect_lan_ip()
    subnet = ipaddress.ip_network(compute_subnet(lan_ip), strict=False)
    allowed: list[IPNetwork] = [subnet]
    for cidr in config.EXTRA_ALLOWED_SUBNETS:
        try:
            allowed.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass

    info = NetworkInfo(lan_ip=lan_ip, subnet=subnet, allowed_networks=tuple(allowed))
    _cache = (now, info)
    return info


def is_host_ip(ip_str: str) -> bool:
    ip = _parse_ip(ip_str)
    if ip is None:
        return False
    return ip.is_loopback or str(ip) == get_network_info().lan_ip


def is_lan_ip_allowed(ip_str: str, lecturer_ip: str | None = None, lecturer_subnet: str | None = None) -> bool:
    if lecturer_ip or lecturer_subnet:
        return is_same_classroom_net(ip_str, lecturer_ip, lecturer_subnet)
    ip = _parse_ip(ip_str)
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    return any(ip.version == net.version and ip in net for net in get_network_info().allowed_networks)


class LanGuardMiddleware(BaseHTTPMiddleware):
    """Records the resolved client IP on ``request.state.client_ip``.

    Loading the Lecturer Dashboard and viewing the Student Check-in page are never
    blocked here; classroom network verification is enforced specifically on
    ``POST /api/attendance/checkin`` against the active session's recorded /24 subnet.
    """

    async def dispatch(self, request: Request, call_next):
        request.state.client_ip = get_client_ip(request)
        return await call_next(request)


def require_lecturer_host(request: Request = None) -> None:
    """Verify lecturer PIN if provided in headers; allows seamless cloud & tunnel usage."""
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
    """Strict lecturer PIN dependency for Phase 2 lecturer-only actions."""
    pin = (request.headers.get("x-lecturer-pin") or request.query_params.get("pin") or "").strip()
    if pin != config.LECTURER_PIN:
        raise HTTPException(
            status_code=403,
            detail="Lecturer access required. Please unlock Lecturer Mode with PIN 3358.",
        )
    return None
