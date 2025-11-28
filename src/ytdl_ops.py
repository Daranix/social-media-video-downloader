from typing import Optional, Any, TypedDict
import os
import uuid
import yt_dlp
import tempfile
import threading
import asyncio
import shutil
from fastapi import HTTPException

from .config import TEMP_DIR, CACHE_TTL_SECONDS
from .utils import generate_video_hash, extract_video_id
from .models import VideoDownloadOptions



class VideoInfo(TypedDict):
    title: Optional[str]
    duration: Optional[int]
    uploader: Optional[str]
    thumbnail: Optional[str]
    description: Optional[str]
    view_count: Optional[int]
    like_count: Optional[int]
    upload_date: Optional[str]
    platform: str
    video_id: Optional[str]
    video_hash: str
    url: str

class VideoDownloadData(TypedDict):
    output_file: str
    video_info: VideoInfo


def extract_video_info(url: str):
    """Extract video information without downloading."""
    try:
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info = ydl.extract_info(url, download=False)
            return build_video_info(url, info) # type: ignore
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract info: {str(e)}")



def build_video_info(url: str, info: dict) -> VideoInfo:
    """Extract video information without downloading."""
    
    platform = info.get("extractor", "unknown")
    video_id = extract_video_id(info)
    video_hash = generate_video_hash(platform, video_id)

    return {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "thumbnail": info.get("thumbnail"),
        "description": info.get("description"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "upload_date": info.get("upload_date"),
        "platform": platform,
        "video_id": video_id,
        "video_hash": video_hash,
        "url": url
    }

def download_video(file_path: str, options: VideoDownloadOptions) -> str:
    """Download video and return ONLY the final merged file."""

    temp_dir = tempfile.mkdtemp(prefix="smvd_", dir=TEMP_DIR)

    ydl_opts = build_ytdl_options(temp_dir, options)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
        info = ydl.extract_info(options.url, download=True)
        output = ydl.prepare_filename(info)

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
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),      # output filename template
        #"ignoreerrors": True,                # continue on download errors
        #"progress_hooks": [lambda d: print(d)], # prints progress similar to CLI
        #"quiet": False,                      # show logs like CLI
        #"no_warnings": False,                 # show warnings
    }

    return ydl_opts
