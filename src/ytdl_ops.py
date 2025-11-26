from typing import Optional, Any
import os
import yt_dlp
from fastapi import HTTPException

from .config import TEMP_DIR
from .utils import generate_video_hash, extract_video_id
from .models import VideoDownloadRequest


def build_yt_dlp_format_string(request: VideoDownloadRequest) -> str:
    """Build yt-dlp format string based on request parameters.
    
    Intelligently builds format strings with graceful fallbacks when exact specs aren't available.
    """
    quality_map = {
        "best": "best",
        "high": "bestvideo+bestaudio",
        "medium": "bestvideo[height<=720]+bestaudio",
        "low": "bestvideo[height<=480]+bestaudio",
        "worst": "worst"
    }

    format_code = request.format_code
    quality = request.quality
    file_format = request.file_format
    resolution = request.resolution

    # If specific format code provided, use it
    if format_code:
        return format_code

    # Build format string based on quality and resolution
    if request.audio_only:
        # For audio-only, get best audio in any format and convert later if needed
        return "bestaudio/best"

    if request.video_only:
        # For video-only, get best video without audio
        if resolution:
            height = resolution.split('x')[1]
            # Try exact height, then fall back to best available
            return f"bestvideo[height<={height}]/bestvideo[height<={int(height)//2}]/bestvideo"
        return "bestvideo/best"

    # Default: video + audio with smart fallbacks
    base_format = quality_map.get(quality, "best")

    if resolution:
        height = resolution.split('x')[1]
        # Build format with multiple fallback options:
        # 1. Try exact resolution with video+audio
        # 2. Try video below resolution + best audio
        # 3. Try any video + best audio
        # 4. Fall back to best overall format
        return f"bestvideo[height<={height}]+bestaudio/bestvideo[height<={int(height)//2}]+bestaudio/bestvideo+bestaudio/{base_format}"

    return f"{base_format}/best"


def extract_video_info(url: str) -> dict:
    """Extract video information without downloading."""
    try:
        ydl_opts: dict[str, Any] = {
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info = ydl.extract_info(url, download=False)
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract info: {str(e)}")


def download_video(url: str, file_path: str) -> str:
    """Download video to specified path."""
    try:
        # Extract directory and use yt-dlp's default filename
        output_dir = os.path.dirname(file_path) if os.path.dirname(file_path) else TEMP_DIR
        ydl_opts: dict[str, Any] = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
            info = ydl.extract_info(url, download=True)
            actual_file = ydl.prepare_filename(info)
            return actual_file
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download video: {str(e)}")


def download_video_advanced(url: str, file_path: str, request: VideoDownloadRequest) -> str:
    """Download video with advanced options and intelligent fallbacks."""
    output_dir = os.path.dirname(file_path) if os.path.dirname(file_path) else TEMP_DIR
    
    # Build list of format strings to try in order
    format_attempts = [build_yt_dlp_format_string(request)]
    
    # Add fallback formats if resolution was specified
    if request.resolution and not request.format_code:
        # If resolution-specific format fails, try without resolution constraint
        format_attempts.append("bestvideo+bestaudio/best")
        # Last resort: just get the best available
        format_attempts.append("best")
    
    last_error = None
    
    for format_string in format_attempts:
        try:
            ydl_opts: dict[str, Any] = {
                'format': format_string,
                'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [],
            }

            # Add FPS limiter if specified
            if request.fps:
                ydl_opts['postprocessors'].append({
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': request.file_format,
                })

            # Add audio quality postprocessor
            if not request.video_only and request.audio_quality:
                ydl_opts['postprocessors'].append({
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3' if request.file_format == 'mp3' else 'aac',
                    'preferredquality': request.audio_quality,
                })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore
                info = ydl.extract_info(url, download=True)
                actual_file = ydl.prepare_filename(info)
                return actual_file
        except Exception as e:
            last_error = e
            # Try next format if available
            if format_string != format_attempts[-1]:
                continue
    
    # If all attempts failed, raise the last error
    error_msg = f"Failed to download video with formats {format_attempts}: {str(last_error)}"
    raise HTTPException(status_code=400, detail=error_msg)
