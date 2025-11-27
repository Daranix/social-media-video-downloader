from typing import Optional, Any
import os
import uuid
import yt_dlp
from fastapi import HTTPException

from .config import TEMP_DIR
from .utils import generate_video_hash, extract_video_id
from .models import VideoDownloadOptions


def extract_video_info(url: str) -> dict:
    """Extract video information without downloading."""
    try:
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            return build_video_info(url, ydl)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract info: {str(e)}")



def build_video_info(url: str, ydlInstance: yt_dlp.YoutubeDL) -> dict:
    """Extract video information without downloading."""
    
    info = ydlInstance.extract_info(url, download=False)
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
    output_dir = os.path.dirname(file_path) if os.path.dirname(file_path) else TEMP_DIR

    ydl_opts = build_ytdl_options(options)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
        info = ydl.extract_info(options.url, download=True)
        

        # yt-dlp returns the pre-merged file (WRONG)
        temp_path = ydl.prepare_filename(info)

        # Build the actual merged output path (RIGHT)
        final_path = os.path.splitext(temp_path)[0] + f".{options.file_format}"

        # If merged file exists → return it
        if os.path.exists(final_path):
            return final_path

        # If merge didn't happen, fallback
        if os.path.exists(temp_path):
            return temp_path

        raise RuntimeError(f"Final output not found: {final_path}")



def build_ytdl_options(opts: VideoDownloadOptions):
    """Build yt-dlp options with progressive priority + fallback merging."""
    
    random_name = uuid.uuid4().hex

    # ---- FORMAT SELECTION ----
    if opts.audio_only:
        selected_format = "bestaudio"
    elif opts.video_only:
        selected_format = "bestvideo"
    else:
        # First try progressive MP4 with audio
        selected_format = "bv+ba"

    # ---- YTDL OPTIONS ----
    ydl_opts = {
        "format": selected_format,                    # equivalent to -f best
        "merge_output_format": "mp4",        # merges audio+video if separate
        "writesubtitles": False,              # writes subtitles if available
        "writeautomaticsub": False,           # downloads automatic subtitles
        "outtmpl": "%(title)s.%(ext)s",      # output filename template
        #"ignoreerrors": True,                # continue on download errors
        #"progress_hooks": [lambda d: print(d)], # prints progress similar to CLI
        #"quiet": False,                      # show logs like CLI
        #"no_warnings": False,                 # show warnings
    }

    return ydl_opts
