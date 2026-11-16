from typing import Optional
from pydantic import BaseModel, field_validator


class VideoInfo(BaseModel):
    """Video information model"""
    id: str
    url: str
    title: Optional[str] = None
    duration: Optional[float] = None
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

    @field_validator("quality", "file_format")
    @classmethod
    def _validate_header_safe_str(cls, value: str) -> str:
        """Reject control characters and non-Latin-1 text so response headers stay safe."""
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("must not contain control characters")
        try:
            value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError("must contain only Latin-1 characters") from exc
        return value

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
