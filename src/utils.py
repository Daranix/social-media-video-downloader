import hashlib
import ipaddress
import socket
from typing import Optional, Any
import urllib.parse
import uuid

from fastapi import HTTPException
import yt_dlp

_NAT64_WELL_KNOWN_NETWORK = ipaddress.IPv6Network("64:ff9b::/96")
_DISALLOWED_HOSTNAMES = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "host.docker.internal",
        "metadata.google.internal",
    }
)
_DISALLOWED_HOST_SUFFIXES = (".localhost", ".local", ".internal")


def _parse_ip_literal(hostname: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Parse standard IPv4/IPv6 or alternative IPv4 literals (decimal/octal/hex/shorthand)."""
    ip_candidate = hostname.split("%", 1)[0]
    try:
        return ipaddress.ip_address(ip_candidate)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(hostname))
    except OSError:
        return None


def _is_disallowed_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if an IP or any embedded transition IPv4 address is non-global/unsafe."""
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip_obj]
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            candidates.append(ip_obj.ipv4_mapped)
        if ip_obj.sixtofour is not None:
            candidates.append(ip_obj.sixtofour)
        if ip_obj.teredo is not None:
            candidates.extend(ip_obj.teredo)
        if ip_obj in _NAT64_WELL_KNOWN_NETWORK:
            candidates.append(ipaddress.IPv4Address(ip_obj.packed[-4:]))

    return any(
        not addr.is_global
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        for addr in candidates
    )


def validate_media_url(url: str) -> str:
    """Validate that url is safe public http(s) without control chars, userinfo, or local/private hosts."""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise HTTPException(status_code=400, detail="Invalid URL: control characters are not allowed")

    stripped = url.strip()
    try:
        parsed = urllib.parse.urlparse(stripped)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc or ""
        username = parsed.username
        password = parsed.password
        raw_hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid URL") from exc

    if scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL: only http and https URLs are supported",
        )

    if "\\" in netloc or any(ch.isspace() for ch in netloc):
        raise HTTPException(status_code=400, detail="Invalid URL: invalid characters in host")

    if username is not None or password is not None or "@" in netloc:
        raise HTTPException(
            status_code=400,
            detail="Invalid URL: embedded credentials are not allowed",
        )

    hostname = (raw_hostname or "").rstrip(".").lower()
    if not hostname or "\\" in hostname or any(ch.isspace() for ch in hostname):
        raise HTTPException(status_code=400, detail="Invalid URL: missing or invalid hostname")

    if hostname in _DISALLOWED_HOSTNAMES or hostname.endswith(_DISALLOWED_HOST_SUFFIXES):
        raise HTTPException(
            status_code=400,
            detail="Invalid URL: local or private host addresses are not allowed",
        )

    ip_obj = _parse_ip_literal(hostname)
    if ip_obj is not None:
        if _is_disallowed_ip(ip_obj):
            raise HTTPException(
                status_code=400,
                detail="Invalid URL: local or private IP addresses are not allowed",
            )
        return stripped

    if "." not in hostname:
        raise HTTPException(
            status_code=400,
            detail="Invalid URL: single-label hostnames are not allowed",
        )

    return stripped


def redact_url_userinfo(url: Optional[str]) -> Optional[str]:
    """Redact userinfo from a URL if present, preserving scheme/host/path."""
    if not url or "@" not in url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.username is None and parsed.password is None and "@" not in (parsed.netloc or ""):
            return url
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return url
    redacted_netloc = f"***:***@{host}{port}"
    return urllib.parse.urlunparse(parsed._replace(netloc=redacted_netloc))


def generate_video_hash(platform: str, video_id: Optional[str]) -> str:
    """Generate deterministic hash based on platform and video ID."""
    vid = video_id or "unknown"
    combined = f"{platform}_{vid}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def extract_video_id(info: dict) -> str:
    """Extract video ID from yt-dlp info dict."""
    extractor = info.get("extractor") or "unknown"
    raw_id = info.get("id")
    video_id = str(raw_id) if raw_id not in (None, "") else uuid.uuid4().hex
    return f"{extractor}_{video_id}"


def detect_platform(url: str) -> str:
    """Detect platform from URL using yt-dlp extractor info."""
    try:
        validated_url = validate_media_url(url)
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info = ydl.extract_info(validated_url, download=False)
            if not isinstance(info, dict):
                return "unknown"
            return info.get("extractor") or "unknown"
    except Exception:
        return "unknown"
