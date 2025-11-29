from typing import Optional
from pydantic import BaseModel


class VideoInfo(BaseModel):
    """Video information model"""
    video_hash: str
    url: str
    title: Optional[str] = None
    duration: Optional[int] = None
    uploader: Optional[str] = None
    thumbnail: Optional[str] = None
    description: Optional[str] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    upload_date: Optional[str] = None
    platform: Optional[str] = None
    video_id: Optional[str] = None


class VideoDownloadOptions(BaseModel):
    """Advanced video download request model"""
    url: str
    format_code: Optional[str] = None
    quality: str = "best"  # "best", "worst", "high", "medium", "low"
    file_format: str = "mp4"  # "mp4", "mkv", "webm", "m4a", "wav", "mp3"
    audio_only: bool = False
    video_only: bool = False
    fps: Optional[int] = None
    resolution: Optional[str] = None  # e.g., "1920x1080"
    audio_quality: str = "192"  # kbps

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "quality": "high",
                "file_format": "mp4",
                "audio_only": False,
                "resolution": "1920x1080"
            }
        }
