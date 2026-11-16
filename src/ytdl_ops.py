from contextlib import contextmanager
import os
import re
import shutil
import tempfile
import threading
import time
from typing import Any, Iterator, Optional
import urllib.parse

from fastapi import HTTPException
import yt_dlp
from yt_dlp.cookies import CookieLoadError
from yt_dlp.networking.exceptions import (
    CertificateVerifyError,
    HTTPError,
    NoSupportingHandlers,
    ProxyError,
    RequestError,
    SSLError,
    TransportError,
    UnsupportedRequest,
)
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import (
    DownloadError,
    EntryNotInPlaylist,
    ExtractorError,
    GeoRestrictedError,
    UnavailableVideoError,
    UnsupportedError,
    UserNotLive,
    YoutubeDLError,
)

from src.cache.cache_registry import CacheRegistry
from src.models.video_cache import VideoCacheData
from src.models.video_data import VideoDownloadOptions, VideoInfo

from . import config
from .utils import extract_video_id, validate_media_url

DEFAULT_DOWNLOAD_RETRIES = 3
DEFAULT_SOCKET_TIMEOUT = 20
DEFAULT_FRAGMENT_RETRIES = 3

_INSTAGRAM_DOMAINS = ("instagram.com", "instagr.am")
_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_YOUTUBE_EXTRACTOR_ARGS = {"youtube": {"player_client": ["default", "ios", "android_vr"]}}
_IMPERSONATE_DISABLED_VALUES = frozenset({"off", "false", "0", "none"})
_SENSITIVE_INFO_KEYS = frozenset(
    {
        "http_headers",
        "cookies",
        "filepath",
        "_filename",
        "filename",
        "requested_downloads",
        "infojson_filename",
    }
)

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_NEWLINES_RE = re.compile(r"[\r\n]+")
_URL_CREDENTIALS_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^\s/]+)(@)")
_TEMP_COOKIE_PATH_RE = re.compile(
    r"(?:(?:<temp_dir>|(?:[A-Za-z]:)?(?:[/\\][^/\\\r\n:*?\"<>|]+)+)[/\\])?"
    r"smvd_cookies_[^\s/'\"]+(?:[/\\][^\s'\"]*)?"
)
_TEMP_WORK_PATH_RE = re.compile(
    r"(?:(?:<temp_dir>|(?:[A-Za-z]:)?(?:[/\\][^/\\\r\n:*?\"<>|]+)+)[/\\])?"
    r"smvd_[^\s/'\"]+(?:[/\\][^\s'\"]*)?"
)
_HTTP_5XX_RE = re.compile(r"\bhttp error 5\d{2}\b")

_CLEANUP_LOCK = threading.Lock()
_CLEANUP_WAKE = threading.Event()
_PENDING_CLEANUPS: dict[str, float] = {}
_ACTIVE_LEASES: dict[str, int] = {}
_DELETING_DIRS: set[str] = set()
_SWEEPER_THREAD: Optional[threading.Thread] = None


class YTDLPConfigurationError(RuntimeError):
    """Raised when server-side yt-dlp configuration is invalid or unusable."""


def _normalize_dir_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _is_confined_to_dir(candidate: str, base_dir: str) -> bool:
    """Return True if candidate resolves inside base_dir."""
    try:
        real_base = os.path.realpath(base_dir)
        real_candidate = os.path.realpath(candidate)
        return os.path.commonpath([real_base, real_candidate]) == real_base
    except (ValueError, OSError):
        return False


def _sweeper_interval() -> float:
    raw = config.CACHE_CLEANUP_INTERVAL
    try:
        configured = 300 if raw is None else int(raw)
    except Exception:
        configured = 300
    return float(min(max(1, configured), 30))


def _finish_claimed_delete(key: str, *, reschedule_delay: float = 5.0) -> None:
    """Delete a directory already claimed in _DELETING_DIRS and clear or reschedule."""
    removed = False
    try:
        removed = _try_remove_path(key)
    finally:
        should_wake = False
        with _CLEANUP_LOCK:
            _DELETING_DIRS.discard(key)
            if not removed:
                _PENDING_CLEANUPS[key] = time.monotonic() + reschedule_delay
                _ensure_sweeper_locked()
                should_wake = True
        if should_wake:
            _CLEANUP_WAKE.set()


def _sweep_once(now: Optional[float] = None) -> None:
    """Remove due unleased temporary directories; retain failures for retry."""
    current = time.monotonic() if now is None else now
    claimed_keys: list[str] = []
    with _CLEANUP_LOCK:
        for key, deadline in list(_PENDING_CLEANUPS.items()):
            if (
                deadline <= current
                and _ACTIVE_LEASES.get(key, 0) == 0
                and key not in _DELETING_DIRS
            ):
                _PENDING_CLEANUPS.pop(key, None)
                _DELETING_DIRS.add(key)
                claimed_keys.append(key)

    for key in claimed_keys:
        _finish_claimed_delete(key, reschedule_delay=5.0)


def _next_sweeper_deadline_locked() -> Optional[float]:
    """Return earliest deadline among unleased, non-deleting cleanups (caller holds _CLEANUP_LOCK)."""
    return min(
        (
            deadline
            for key, deadline in _PENDING_CLEANUPS.items()
            if _ACTIVE_LEASES.get(key, 0) == 0 and key not in _DELETING_DIRS
        ),
        default=None,
    )


def _sweeper_loop() -> None:
    while True:
        with _CLEANUP_LOCK:
            next_deadline = _next_sweeper_deadline_locked()
        max_wait = _sweeper_interval()
        if next_deadline is not None:
            wait_seconds = max(0.05, min(max_wait, next_deadline - time.monotonic()))
        else:
            wait_seconds = max_wait
        _CLEANUP_WAKE.wait(timeout=wait_seconds)
        _CLEANUP_WAKE.clear()
        try:
            _sweep_once()
        except Exception:
            pass


def _ensure_sweeper_locked() -> None:
    global _SWEEPER_THREAD
    if _SWEEPER_THREAD is None or not _SWEEPER_THREAD.is_alive():
        _SWEEPER_THREAD = threading.Thread(
            target=_sweeper_loop,
            name="smvd-temp-sweeper",
            daemon=True,
        )
        _SWEEPER_THREAD.start()


def acquire_temp_dir(dir_path: str) -> bool:
    """Increment the active lease count unless the directory is already being deleted."""
    if not dir_path:
        return False
    key = _normalize_dir_key(dir_path)
    with _CLEANUP_LOCK:
        if key in _DELETING_DIRS:
            return False
        _ACTIVE_LEASES[key] = _ACTIVE_LEASES.get(key, 0) + 1
        return True


def release_temp_dir_lease(dir_path: str, *, remove_now: bool = False) -> bool:
    """
    Decrement active lease synchronously; claim _DELETING_DIRS and return True if
    the caller should perform disk removal via finalize_claimed_delete.
    """
    if not dir_path:
        return False
    key = _normalize_dir_key(dir_path)
    now = time.monotonic()
    with _CLEANUP_LOCK:
        count = _ACTIVE_LEASES.get(key, 0)
        if count <= 1:
            _ACTIVE_LEASES.pop(key, None)
            pending_deadline = _PENDING_CLEANUPS.get(key)
            wants_delete = remove_now or (pending_deadline is not None and pending_deadline <= now)
            if wants_delete and key not in _DELETING_DIRS:
                _PENDING_CLEANUPS.pop(key, None)
                _DELETING_DIRS.add(key)
                return True
        else:
            _ACTIVE_LEASES[key] = count - 1
    return False


def finalize_claimed_delete(dir_path: str, *, reschedule_delay: float = 1.0) -> None:
    """Complete removal for a directory claimed via release_temp_dir_lease."""
    if not dir_path:
        return
    _finish_claimed_delete(_normalize_dir_key(dir_path), reschedule_delay=reschedule_delay)


def release_temp_dir(dir_path: str, *, remove_now: bool = False) -> None:
    """Release a temp-dir lease and remove the directory if requested or overdue."""
    if release_temp_dir_lease(dir_path, remove_now=remove_now):
        finalize_claimed_delete(dir_path, reschedule_delay=1.0)


def _schedule_cleanup(path: str, delay: int) -> None:
    """Register a temporary directory for deferred cleanup by the sweeper thread."""
    if delay <= 0:
        return
    key = _normalize_dir_key(path)
    deadline = time.monotonic() + delay
    with _CLEANUP_LOCK:
        _PENDING_CLEANUPS[key] = deadline
        _ensure_sweeper_locked()
    _CLEANUP_WAKE.set()


def schedule_managed_dir_cleanup(dir_path: str) -> None:
    """Clean up a managed smvd_* directory immediately if unleased, or right after lease release."""
    if not dir_path:
        return
    key = _normalize_dir_key(dir_path)
    if not os.path.basename(key).startswith("smvd_"):
        return
    if not _is_confined_to_dir(key, config.TEMP_DIR):
        return

    claimed = False
    with _CLEANUP_LOCK:
        if key in _DELETING_DIRS:
            return
        if _ACTIVE_LEASES.get(key, 0) > 0:
            _PENDING_CLEANUPS[key] = time.monotonic()
            _ensure_sweeper_locked()
        else:
            _PENDING_CLEANUPS.pop(key, None)
            _DELETING_DIRS.add(key)
            claimed = True

    if not claimed:
        _CLEANUP_WAKE.set()
        return

    _finish_claimed_delete(key, reschedule_delay=1.0)


def cleanup_stale_temp_dirs() -> None:
    """Sweep leftover smvd_* and smvd_cookies_* directories in TEMP_DIR on startup."""
    temp_root = config.TEMP_DIR
    if not temp_root or not os.path.isdir(temp_root):
        return
    try:
        with os.scandir(temp_root) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if not entry.name.startswith("smvd_"):
                    continue
                key = _normalize_dir_key(entry.path)
                with _CLEANUP_LOCK:
                    if _ACTIVE_LEASES.get(key, 0) > 0 or key in _DELETING_DIRS:
                        continue
                    _PENDING_CLEANUPS.pop(key, None)
                    _DELETING_DIRS.add(key)
                try:
                    _try_remove_path(key)
                finally:
                    with _CLEANUP_LOCK:
                        _DELETING_DIRS.discard(key)
    except OSError:
        pass


def reset_cleanup_state() -> None:
    """Clear pending cleanup registrations, active leases, and deleting claims (for test isolation)."""
    with _CLEANUP_LOCK:
        _PENDING_CLEANUPS.clear()
        _ACTIVE_LEASES.clear()
        _DELETING_DIRS.clear()


def _parse_url_with_optional_scheme(url: str) -> urllib.parse.ParseResult:
    """Parse a URL, allowing schemeless host[:port]/path strings to resolve a hostname."""
    stripped = url.strip()
    if "://" not in stripped and not stripped.lower().startswith(
        ("http:", "https:", "file:", "ftp:", "javascript:", "data:", "mailto:")
    ):
        return urllib.parse.urlparse(f"https://{stripped}")
    return urllib.parse.urlparse(stripped)


def _extract_hostname(url: str) -> str:
    """Return the normalized lowercased hostname without trailing dots."""
    parsed = _parse_url_with_optional_scheme(url)
    return (parsed.hostname or "").rstrip(".").lower()


def _is_hostname_match(hostname: str, domains: tuple[str, ...]) -> bool:
    """Check whether hostname matches any domain or its subdomains."""
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _resolve_impersonate_target(
    url: str,
    impersonate_override: Optional[str],
) -> Optional[ImpersonateTarget]:
    """
    Resolve a safe yt-dlp ImpersonateTarget without conflicting with YouTube clients.

    - ``off`` (or disabled aliases): never set ``impersonate`` in ``ydl_opts``.
    - ``auto`` or unset: set ``ImpersonateTarget()`` for Instagram URLs only;
      YouTube and other URLs omit global ``impersonate`` so YouTube's
      ``player_client: ['default', 'ios', 'android_vr']`` is unaffected.
    - Explicit target string (e.g. ``"chrome"``): parse with
      ``ImpersonateTarget.from_str`` and apply to non-YouTube URLs while still
      protecting YouTube's custom ``player_client`` headers.
    """
    normalized = config._normalize_optional_str(impersonate_override)
    normalized_lower = normalized.lower() if normalized is not None else None
    if normalized_lower in _IMPERSONATE_DISABLED_VALUES:
        return None

    hostname = _extract_hostname(url)
    is_youtube = _is_hostname_match(hostname, _YOUTUBE_DOMAINS)

    if normalized_lower is None or normalized_lower == "auto":
        if _is_hostname_match(hostname, _INSTAGRAM_DOMAINS):
            return ImpersonateTarget()
        return None

    try:
        target = ImpersonateTarget.from_str(normalized_lower)
    except ValueError as exc:
        raise YTDLPConfigurationError("Invalid YTDLP_IMPERSONATE target") from exc

    if target == ImpersonateTarget():
        raise YTDLPConfigurationError("Invalid YTDLP_IMPERSONATE target")

    if is_youtube:
        return None
    return target


def _chmod_best_effort(path: str, mode: int) -> None:
    """Apply restrictive POSIX permissions where supported."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _ensure_temp_dir() -> str:
    """Ensure configured TEMP_DIR exists and is accessible."""
    try:
        os.makedirs(config.TEMP_DIR, exist_ok=True)
    except OSError as exc:
        raise YTDLPConfigurationError("Temporary directory is not accessible") from exc
    return config.TEMP_DIR


@contextmanager
def _prepared_cookiefile(cookies_file: Optional[str] = None) -> Iterator[Optional[str]]:
    """
    Validate configured COOKIES_FILE and copy it to an isolated temp file.

    YoutubeDL writes updated cookies back to ``cookiefile`` on close, so copying
    to a fresh writable per-operation file avoids PermissionError on read-only
    Docker mounts and prevents race conditions across concurrent workers.
    """
    source = cookies_file if cookies_file is not None else config.COOKIES_FILE
    if not source:
        yield None
        return

    if not os.path.isfile(source):
        raise YTDLPConfigurationError(
            "Configured COOKIES_FILE does not exist or is not a regular file."
        )

    temp_root = _ensure_temp_dir()
    try:
        cookie_tmp_dir = tempfile.mkdtemp(prefix="smvd_cookies_", dir=temp_root)
    except OSError as exc:
        raise YTDLPConfigurationError("Temporary directory is not accessible") from exc

    _chmod_best_effort(cookie_tmp_dir, 0o700)
    temp_cookie_path = os.path.join(cookie_tmp_dir, "cookies.txt")
    try:
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            with open(source, "rb") as src_fh:
                fd = os.open(temp_cookie_path, flags, 0o600)
                try:
                    dst_fh = os.fdopen(fd, "wb")
                except Exception:
                    os.close(fd)
                    raise
                with dst_fh:
                    shutil.copyfileobj(src_fh, dst_fh)
            _chmod_best_effort(temp_cookie_path, 0o600)
        except OSError as exc:
            raise YTDLPConfigurationError(
                "Failed to prepare configured COOKIES_FILE for yt-dlp."
            ) from exc
        yield temp_cookie_path
    finally:
        if not _try_remove_path(cookie_tmp_dir):
            _schedule_cleanup(cookie_tmp_dir, 1)


def build_base_ytdl_options(
    url: str,
    *,
    quiet: bool = False,
    cookiefile: Optional[str] = None,
) -> dict[str, Any]:
    """Build shared yt-dlp options for both metadata extraction and downloads."""
    ydl_opts: dict[str, Any] = {
        "extractor_args": {
            "youtube": dict(_YOUTUBE_EXTRACTOR_ARGS["youtube"]),
        },
        "cachedir": False,
        "noplaylist": True,
        "socket_timeout": DEFAULT_SOCKET_TIMEOUT,
        "retries": DEFAULT_DOWNLOAD_RETRIES,
        "fragment_retries": DEFAULT_FRAGMENT_RETRIES,
    }

    if quiet:
        ydl_opts["quiet"] = True
        ydl_opts["no_warnings"] = True

    if config.PROXY_URL:
        ydl_opts["proxy"] = config.PROXY_URL

    if config.YTDLP_SLEEP_REQUESTS is not None:
        ydl_opts["sleep_interval_requests"] = config.YTDLP_SLEEP_REQUESTS

    if config.YTDLP_EXTRACTOR_RETRIES is not None:
        ydl_opts["extractor_retries"] = config.YTDLP_EXTRACTOR_RETRIES

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    impersonate_target = _resolve_impersonate_target(url, config.YTDLP_IMPERSONATE)
    if impersonate_target is not None:
        ydl_opts["impersonate"] = impersonate_target

    return ydl_opts


def _replace_literal_variants(text: str, raw_value: Optional[str], placeholder: str) -> str:
    """Replace literal and normalized path variants of a sensitive config value."""
    if not raw_value:
        return text
    variants = {raw_value}
    if placeholder != "<proxy_url>":
        variants.add(os.path.normpath(raw_value))
        variants.add(os.path.realpath(raw_value))
    for candidate in sorted((v for v in variants if v and len(v) > 1), key=len, reverse=True):
        text = text.replace(candidate, placeholder)
    return text


def _clean_error_message(exc: BaseException) -> str:
    """Sanitize error text by scrubbing secrets, paths, ANSI escapes, and newlines."""
    raw = str(exc) or type(exc).__name__
    cleaned = _replace_literal_variants(raw, config.PROXY_URL, "<proxy_url>")
    cleaned = _replace_literal_variants(cleaned, config.COOKIES_FILE, "<cookies_file>")
    cleaned = _replace_literal_variants(cleaned, config.TEMP_DIR, "<temp_dir>")

    cleaned = _ANSI_OSC_RE.sub("", cleaned)
    cleaned = _ANSI_CSI_RE.sub("", cleaned)
    cleaned = _NEWLINES_RE.sub(" ", cleaned)
    cleaned = _URL_CREDENTIALS_RE.sub(r"\1***:***\3", cleaned)
    cleaned = _TEMP_COOKIE_PATH_RE.sub("<cookies_file>", cleaned)
    cleaned = _TEMP_WORK_PATH_RE.sub("<temp_dir>", cleaned)
    cleaned = cleaned.strip()
    return cleaned or type(exc).__name__


def _iter_exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield an exception and nested yt-dlp/Python cause exceptions without cycles."""
    seen: set[int] = set()
    queue: list[BaseException] = [exc]
    while queue:
        current = queue.pop(0)
        obj_id = id(current)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        yield current

        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) >= 2 and isinstance(exc_info[1], BaseException):
            queue.append(exc_info[1])
        if isinstance(current, NoSupportingHandlers):
            for nested in (*current.unsupported_errors, *current.unexpected_errors):
                if isinstance(nested, BaseException):
                    queue.append(nested)
        for attr in ("cause", "__cause__", "__context__"):
            nested = getattr(current, attr, None)
            if isinstance(nested, BaseException):
                queue.append(nested)


def map_ytdlp_error(exc: Exception) -> HTTPException:
    """Map yt-dlp, configuration, and unexpected exceptions to HTTPException."""
    if isinstance(exc, HTTPException):
        return exc

    chain = list(_iter_exception_chain(exc))
    if not any(
        isinstance(item, (YTDLPConfigurationError, CookieLoadError, YoutubeDLError, RequestError))
        for item in chain
    ):
        return HTTPException(status_code=500, detail="Internal server error")

    if any(isinstance(item, CookieLoadError) for item in chain):
        return HTTPException(status_code=500, detail="Failed to load configured COOKIES_FILE")

    detail = _clean_error_message(exc)
    if any(isinstance(item, YTDLPConfigurationError) for item in chain):
        return HTTPException(status_code=500, detail=detail)

    lowered = " ".join(_clean_error_message(item).lower() for item in chain)
    http_statuses = {
        item.status
        for item in chain
        if isinstance(item, HTTPError) and isinstance(getattr(item, "status", None), int)
    }

    # 1. Server configuration, impersonation support, proxy config, or ffmpeg -> 500
    if (
        "unsupported impersonate target" in lowered
        or "no impersonate target" in lowered
        or "none of these impersonate targets" in lowered
        or ("impersonate target" in lowered and ("not available" in lowered or "missing dependencies" in lowered))
        or "ffmpeg not found" in lowered
        or "ffprobe and ffmpeg not found" in lowered
        or "unsupported proxy" in lowered
        or "to use an https proxy" in lowered
        or (any(isinstance(item, UnsupportedRequest) for item in chain) and "proxy" in lowered)
    ):
        return HTTPException(status_code=500, detail=detail)

    # 2. Unsupported or invalid URL / requested format -> 400
    if any(isinstance(item, UnsupportedError) for item in chain):
        return HTTPException(status_code=400, detail=detail)
    if any(
        phrase in lowered
        for phrase in (
            "unsupported url",
            "is not a valid url",
            "invalid url",
            "requested format is not available",
            "no video in this post",
            "no video formats found",
            "multi-entry playlists are not supported",
        )
    ):
        return HTTPException(status_code=400, detail=detail)

    # 3. Rate-limit (including Instagram's combined login-redirect + rate-limit message) -> 429
    if 429 in http_statuses or any(
        phrase in lowered
        for phrase in (
            "rate-limit",
            "rate limit",
            "rate_limit",
            "too many requests",
            "http error 429",
        )
    ):
        return HTTPException(status_code=429, detail=detail)

    # 4. Geo-restriction / forbidden -> 403 (checked before 404 since YouTube embeds "Video unavailable")
    if any(isinstance(item, GeoRestrictedError) for item in chain) or 403 in http_statuses or any(
        phrase in lowered
        for phrase in (
            "geo-restricted",
            "geo restriction",
            "not available in your country",
            "not available from your location",
            "http error 403",
        )
    ):
        return HTTPException(status_code=403, detail=detail)

    # 5. Upstream empty response / anti-bot block / network / proxy / 5xx -> 503
    if (
        "empty media response" in lowered
        or 407 in http_statuses
        or "proxy authentication" in lowered
        or "http error 407" in lowered
        or any(isinstance(item, (ProxyError, TransportError, SSLError, CertificateVerifyError)) for item in chain)
        or any(status >= 500 for status in http_statuses)
        or _HTTP_5XX_RE.search(lowered) is not None
    ):
        return HTTPException(status_code=503, detail=detail)

    # 6. Deleted, removed, or unavailable content -> 404
    if any(isinstance(item, (UnavailableVideoError, EntryNotInPlaylist, UserNotLive)) for item in chain):
        return HTTPException(status_code=404, detail=detail)
    if any(status in (404, 410) for status in http_statuses) or any(
        phrase in lowered
        for phrase in (
            "video unavailable",
            "this video is unavailable",
            "content isn't available",
            "post isn't available",
            "has been removed",
            "has been deleted",
            "does not exist",
            "page not found",
            "video not found",
            "post not found",
            "user not found",
            "http error 404",
            "http error 410",
        )
    ):
        return HTTPException(status_code=404, detail=detail)

    # 7. Authentication / login required -> 401
    if 401 in http_statuses or any(
        phrase in lowered
        for phrase in (
            "http error 401",
            "login required",
            "you need to log in",
            "sign in",
            "registered users",
            "restricted video",
            "cookies are no longer valid",
            "use --cookies",
            "private video",
            "this video is private",
            "authentication",
        )
    ):
        return HTTPException(status_code=401, detail=detail)

    # 8. Other transient upstream / network errors -> 503
    if any(
        phrase in lowered
        for phrase in (
            "timed out",
            "timeout",
            "connection refused",
            "connection reset",
            "temporarily unavailable",
            "try again later",
            "proxy error",
            "unable to download webpage",
            "unable to download json",
        )
    ):
        return HTTPException(status_code=503, detail=detail)

    return HTTPException(status_code=502, detail=detail)


def _strip_sensitive_info_keys(obj: Any) -> Any:
    """Recursively strip sensitive or local-path keys from sanitized info structures."""
    if isinstance(obj, dict):
        return {
            key: _strip_sensitive_info_keys(value)
            for key, value in obj.items()
            if key not in _SENSITIVE_INFO_KEYS
        }
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_strip_sensitive_info_keys(item) for item in obj]
    return obj


def _sanitize_raw_info(ydl: Any, info: dict) -> dict:
    """Sanitize yt-dlp info dict for caching/JSON without headers, cookies, or filepaths."""
    sanitize_fn = getattr(ydl, "sanitize_info", None)
    if callable(sanitize_fn):
        shallow = dict(info)
        try:
            sanitized = sanitize_fn(shallow, remove_private_keys=True)
        except TypeError:
            sanitized = sanitize_fn(shallow)
    else:
        sanitized = info
    if not isinstance(sanitized, dict):
        return {}
    stripped = _strip_sensitive_info_keys(sanitized)
    return stripped if isinstance(stripped, dict) else {}


def extract_video_info(url: str):
    """Extract video information without downloading."""
    validated_url = validate_media_url(url)
    try:
        with _prepared_cookiefile() as temp_cookiefile:
            ydl_opts = build_base_ytdl_options(validated_url, quiet=True, cookiefile=temp_cookiefile)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                info: dict = ydl.extract_info(validated_url, download=False)  # type: ignore
                if not isinstance(info, dict):
                    raise ExtractorError("yt-dlp returned empty extraction metadata", expected=False)
                info_result = build_video_info(validated_url, info)
                sanitized_raw_info = _sanitize_raw_info(ydl, info)

                video_cache = CacheRegistry.get_default()

                cache_data: VideoCacheData = {
                    "id": info_result.id,
                    "url": validated_url,
                    "output_path": None,
                    "info": info_result,
                    "raw_info": sanitized_raw_info,
                    "download_options": None,
                }

                ttl = config.CACHE_TTL_SECONDS
                if ttl > 0:
                    video_cache.set(info_result.id, cache_data, ttl=ttl)
                return cache_data

    except HTTPException:
        raise
    except Exception as e:
        raise map_ytdlp_error(e) from e


def build_video_info(url: str, info: dict) -> VideoInfo:
    """Extract video information and return a `VideoInfo` model instance."""

    platform = info.get("extractor") or "unknown"
    video_id = extract_video_id(info)

    return VideoInfo(
        id=video_id,
        url=url,
        title=info.get("title"),
        duration=info.get("duration"),
        uploader=info.get("uploader"),
        thumbnail=info.get("thumbnail"),
        description=info.get("description"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        upload_date=info.get("upload_date"),
        platform=platform,
        video_id=video_id,
    )


def _resolve_downloaded_filepath(ydl: Any, info: dict, temp_dir: str) -> str:
    """Resolve and verify the single downloaded media file confined inside temp_dir."""
    target_info = info
    if info.get("_type") == "playlist" and info.get("entries") is not None:
        entries = list(info.get("entries") or [])
        if len(entries) != 1 or not isinstance(entries[0], dict):
            raise ExtractorError(
                "Multi-entry playlists are not supported for single-file download",
                expected=True,
            )
        target_info = entries[0]

    for source_dict in (target_info, info):
        requested = source_dict.get("requested_downloads")
        if isinstance(requested, list) and requested and isinstance(requested[0], dict):
            candidate = requested[0].get("filepath")
            if (
                isinstance(candidate, str)
                and _is_confined_to_dir(candidate, temp_dir)
                and os.path.isfile(candidate)
            ):
                return candidate

    prepared = ydl.prepare_filename(target_info)
    if isinstance(prepared, str) and prepared:
        if _is_confined_to_dir(prepared, temp_dir) and os.path.isfile(prepared):
            return prepared
        stem, _ = os.path.splitext(prepared)
        mp4_candidate = f"{stem}.mp4"
        if _is_confined_to_dir(mp4_candidate, temp_dir) and os.path.isfile(mp4_candidate):
            return mp4_candidate

    raise ExtractorError("Downloaded media file was not produced", expected=False)


def download_video(options: VideoDownloadOptions) -> str:
    """Download video and return ONLY the final merged file."""
    validated_url = validate_media_url(options.url)
    effective_options = (
        options
        if validated_url == options.url
        else options.model_copy(update={"url": validated_url})
    )
    temp_root = _ensure_temp_dir()
    try:
        temp_dir = tempfile.mkdtemp(prefix="smvd_", dir=temp_root)
    except OSError as exc:
        raise YTDLPConfigurationError("Temporary directory is not accessible") from exc

    try:
        with _prepared_cookiefile() as temp_cookiefile:
            ydl_opts = build_ytdl_options(temp_dir, effective_options, cookiefile=temp_cookiefile)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                info: dict = ydl.extract_info(validated_url, download=True)  # type: ignore
                if not isinstance(info, dict):
                    raise ExtractorError("yt-dlp returned empty extraction metadata", expected=False)
                output = _resolve_downloaded_filepath(ydl, info, temp_dir)
                normalized_info = build_video_info(validated_url, info)
                sanitized_raw_info = _sanitize_raw_info(ydl, info)
                video_id = normalized_info.id

        try:
            ttl = int(config.CACHE_TTL_SECONDS)
        except Exception:
            ttl = 3600

        if ttl > 0:
            cache_video = CacheRegistry.get_default()
            cache_data: VideoCacheData = {
                "id": video_id,
                "url": validated_url,
                "output_path": output,
                "info": normalized_info,
                "raw_info": sanitized_raw_info,
                "download_options": effective_options,
            }
            cache_video.set(video_id, cache_data, ttl=ttl)
            _schedule_cleanup(temp_dir, ttl)

        return output
    except Exception:
        if not _try_remove_path(temp_dir):
            _schedule_cleanup(temp_dir, 1)
        raise


def _try_remove_path(p: str) -> bool:
    """Attempt best-effort removal; return True if removed or absent, False otherwise."""
    try:
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
        return not os.path.exists(p)
    except OSError:
        return not os.path.exists(p)


def _remove_path(p: str) -> None:
    _try_remove_path(p)


def build_ytdl_options(
    output_dir: str,
    opts: VideoDownloadOptions,
    *,
    cookiefile: Optional[str] = None,
) -> dict[str, Any]:
    """Build yt-dlp options with progressive priority + fallback merging."""

    # ---- FORMAT SELECTION ----
    if opts.audio_only:
        selected_format = "bestaudio"
    elif opts.video_only:
        selected_format = "bestvideo"
    else:
        selected_format = "bv+ba/best"

    # ---- YTDL OPTIONS ----
    ydl_opts = build_base_ytdl_options(opts.url, cookiefile=cookiefile)
    ydl_opts.update(
        {
            "format": selected_format,                    # equivalent to -f best
            "writesubtitles": False,              # writes subtitles if available
            "writeautomaticsub": False,           # downloads automatic subtitles
            "outtmpl": os.path.join(output_dir, "%(extractor)s_%(id)s.%(ext)s"),
            #"ignoreerrors": True,                # continue on download errors
            #"progress_hooks": [lambda d: print(d)], # prints progress similar to CLI
            #"quiet": False,                      # show logs like CLI
            #"no_warnings": False,                 # show warnings
        }
    )
    if not opts.audio_only:
        ydl_opts["merge_output_format"] = "mp4"

    return ydl_opts


def get_default_download_options(url: str) -> VideoDownloadOptions:
    """Get default options for downloading videos."""
    return VideoDownloadOptions(
        url=url,
        quality="best",
    )
