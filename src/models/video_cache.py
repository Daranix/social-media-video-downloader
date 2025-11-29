from typing import Optional, TypedDict

from src.models.video_data import VideoDownloadOptions, VideoInfo

class VideoCacheData(TypedDict):
    id: str
    url: str
    output_path: Optional[str]
    info: VideoInfo
    raw_info: dict
    download_options: Optional[VideoDownloadOptions]