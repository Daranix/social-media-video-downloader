from typing import Optional, Any
import yt_dlp
from fastapi import HTTPException

from .utils import generate_video_hash, extract_video_id
from .models import VideoDownloadRequest


def build_yt_dlp_format_string(request: VideoDownloadRequest) -> str:
    """Build yt-dlp format string based on request parameters."""
    quality_map = {
        "best": "best",
        "high": "bestvideo[height>=1080]+bestaudio/best",
        "medium": "bestvideo[height>=720]+bestaudio/best",
        "low": "bestvideo[height>=480]+bestaudio/best",
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
        return f"bestaudio[ext={file_format}]/bestaudio"

    if request.video_only:
        if resolution:
            return f"bestvideo[ext={file_format}][height={resolution.split('x')[1]}]/bestvideo[ext={file_format}]"
        return f"bestvideo[ext={file_format}]/bestvideo"

    # Default: video + audio
    base_format = quality_map.get(quality, "best")

    if resolution:
        height = resolution.split('x')[1]
        return f"bestvideo[ext={file_format}][height={height}]+bestaudio[ext=m4a]/best[ext={file_format}]/{base_format}"

    return f"{base_format}[ext={file_format}]/best[ext={file_format}]"


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
        ydl_opts: dict[str, Any] = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': file_path,
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
    """Download video with advanced options."""
    try:
        format_string = build_yt_dlp_format_string(request)

        ydl_opts: dict[str, Any] = {
            'format': format_string,
            'outtmpl': file_path,
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
        raise HTTPException(status_code=400, detail=f"Failed to download video: {str(e)}")
