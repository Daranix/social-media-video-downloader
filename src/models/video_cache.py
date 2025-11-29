from typing import TypedDict

from src.models.video_data import VideoDownloadOptions, VideoInfo

class VideoCacheData(TypedDict):
    id: str
    url: str
    output_path: str
    info: VideoInfo
    raw_info: dict
    download_options: VideoDownloadOptions