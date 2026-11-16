import math
import os
import tempfile
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()


def _normalize_optional_str(value: Optional[str]) -> Optional[str]:
    """Strip surrounding whitespace and convert empty strings to None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _get_optional_env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an optional string environment variable, normalizing empty values to None."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return _normalize_optional_str(raw)


def _get_optional_non_negative_float(name: str, default: Optional[float] = None) -> Optional[float]:
    """Read an optional non-negative finite float environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = _normalize_optional_str(raw)
    if normalized is None:
        return None
    try:
        value = float(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number, got {raw!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a non-negative finite number, got {raw!r}")
    return value


def _get_optional_non_negative_int(name: str, default: Optional[int] = None) -> Optional[int]:
    """Read an optional non-negative integer environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = _normalize_optional_str(raw)
    if normalized is None:
        return None
    try:
        value = int(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {raw!r}")
    return value


# Default configuration values
_cache_ttl = _get_optional_non_negative_int("CACHE_TTL_SECONDS", default=3600)
CACHE_TTL_SECONDS: int = 3600 if _cache_ttl is None else _cache_ttl  # Default: 1 hour

_cache_cleanup = _get_optional_non_negative_int("CACHE_CLEANUP_INTERVAL", default=300)
CACHE_CLEANUP_INTERVAL: int = 300 if _cache_cleanup is None else _cache_cleanup  # Default: 5 minutes

TEMP_DIR: str = _get_optional_env_str("TEMP_DIR") or tempfile.gettempdir()

# Optional yt-dlp network, cookie, and anti-block settings
COOKIES_FILE: Optional[str] = _get_optional_env_str("COOKIES_FILE")
PROXY_URL: Optional[str] = _get_optional_env_str("PROXY_URL")
YTDLP_IMPERSONATE: Optional[str] = _get_optional_env_str("YTDLP_IMPERSONATE")
YTDLP_SLEEP_REQUESTS: Optional[float] = _get_optional_non_negative_float("YTDLP_SLEEP_REQUESTS", default=0.5)
YTDLP_EXTRACTOR_RETRIES: Optional[int] = _get_optional_non_negative_int("YTDLP_EXTRACTOR_RETRIES", default=3)

_max_jobs = _get_optional_non_negative_int("YTDLP_MAX_CONCURRENT_JOBS", default=4)
if _max_jobs is not None and _max_jobs < 1:
    raise ValueError(f"YTDLP_MAX_CONCURRENT_JOBS must be a positive integer, got {_max_jobs!r}")
YTDLP_MAX_CONCURRENT_JOBS: int = 4 if _max_jobs is None else _max_jobs
