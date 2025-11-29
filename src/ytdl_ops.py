from typing import Optional, Any, TypedDict
import os
import uuid
import yt_dlp
import tempfile
import threading
import asyncio
import shutil
from fastapi import HTTPException

from src.cache.cache_manager import CacheManager
from src.cache.cache_registry import CacheRegistry
from src.models.video_cache import VideoCacheData
from src.models.video_data import VideoDownloadOptions, VideoInfo

from .config import TEMP_DIR, CACHE_TTL_SECONDS
from .utils import generate_video_hash, extract_video_id


def extract_video_info(url: str):
    """Extract video information without downloading."""
    try:
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info: dict = ydl.extract_info(url, download=False) # type: ignore
            info_result = build_video_info(url, info) # type: ignore

            video_cache = CacheRegistry.get_default()

            cache_data: VideoCacheData = {
                "id": info_result.id,
                "url": url,
                "output_path": None,
                "info": info_result,
                "raw_info": info,
                "download_options": None
            }

            video_cache.set(info_result.id, cache_data)
            return cache_data

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract info: {str(e)}")



def build_video_info(url: str, info: dict) -> VideoInfo:
    """Extract video information and return a `VideoInfo` model instance."""
    
    platform = info.get("extractor", "unknown")
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

def download_video(options: VideoDownloadOptions) -> str:
    """Download video and return ONLY the final merged file."""

    temp_dir = tempfile.mkdtemp(prefix="smvd_", dir=TEMP_DIR)
    ydl_opts = build_ytdl_options(temp_dir, options)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
        info: dict = ydl.extract_info(options.url, download=True) # type: ignore
        normalized_info = build_video_info(options.url, info)
        id = f"{info.get('extractor')}_{info.get('id')}"
        output = ydl.prepare_filename(info) # type: ignore
        

        def _schedule_cleanup(path: str, delay: int):
            loop = asyncio.get_running_loop()
            # schedule and don't await

            async def _async_cleanup():
                try:
                    await asyncio.sleep(delay)
                    _remove_path(path)
                except Exception:
                    pass

            loop.create_task(_async_cleanup())

        # Use configured TTL (seconds) for cleanup; default is defined in config
        try:
            ttl = int(CACHE_TTL_SECONDS)
        except Exception:
            ttl = 3600

        _schedule_cleanup(output, ttl)

        cache_video = CacheRegistry.get_default()
        cache_data: VideoCacheData = {
            "id": id,
            "url": options.url,
            "output_path": output,
            "info": normalized_info,
            "raw_info": info,
            "download_options": options
        }
        cache_video.set(id, cache_data)

        return output

def _remove_path(p: str):
    try:
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    except Exception:
        # Best-effort cleanup; ignore failures
        pass

def build_ytdl_options(output_dir: str, opts: VideoDownloadOptions):
    """Build yt-dlp options with progressive priority + fallback merging."""
    
    # ---- FORMAT SELECTION ----
    if opts.audio_only:
        selected_format = "bestaudio"
    elif opts.video_only:
        selected_format = "bestvideo"
    else:
        selected_format = "bv+ba"

    # ---- YTDL OPTIONS ----
    ydl_opts = {
        "format": selected_format,                    # equivalent to -f best
        "merge_output_format": "mp4",        # merges audio+video if separate
        "writesubtitles": False,              # writes subtitles if available
        "writeautomaticsub": False,           # downloads automatic subtitles
        "outtmpl": os.path.join(output_dir, "%(extractor)s_%(id)s.%(ext)s"),
        #"ignoreerrors": True,                # continue on download errors
        #"progress_hooks": [lambda d: print(d)], # prints progress similar to CLI
        #"quiet": False,                      # show logs like CLI
        #"no_warnings": False,                 # show warnings
    }

    return ydl_opts

def get_default_download_options(url: str) -> VideoDownloadOptions:
    """Get default options for downloading videos."""
    return VideoDownloadOptions(
        url=url,
        quality="best",
    )