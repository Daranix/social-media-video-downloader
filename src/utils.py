import hashlib
from typing import Optional, Any

import yt_dlp


def generate_video_hash(platform: str, video_id: Optional[str]) -> str:
    """Generate deterministic hash based on platform and video ID."""
    vid = video_id or "unknown"
    combined = f"{platform}_{vid}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def extract_video_id(info: Any) -> Optional[str]:
    """Extract video ID from yt-dlp info dict."""
    return info.get("id")


def is_url_valid(url: str) -> bool:
    """Check if URL is valid and supported by yt-dlp."""
    try:
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            ydl.extract_info(url, download=False)
            return True
    except Exception:
        return False


def detect_platform(url: str) -> str:
    """Detect platform from URL using yt-dlp extractor info."""
    try:
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info = ydl.extract_info(url, download=False)
            extractor = info.get("extractor", "unknown")
            return extractor
    except Exception:
        return "unknown"
