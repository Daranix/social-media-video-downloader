import os
import time
import uuid
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.responses import FileResponse

from src.cache.cache_registry import CacheRegistry
from src.models.video_cache import VideoCacheData
from src.models.video_data import VideoDownloadOptions, VideoInfo

from .config import TEMP_DIR
from .ytdl_ops import extract_video_info, download_video, download_video, get_default_download_options


CacheRegistry.create('default', 'in-memory')

app = FastAPI(
    title="Video Downloader API",
    description="Download videos from any platform supported by yt-dlp with automatic caching and metadata extraction",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

@app.get(
    "/api/info",
    response_model=VideoCacheData,
    summary="Extract Video Information",
    tags=["Video Operations"]
)
async def extract_info(url: str = Query(..., description="Video URL from any platform supported by yt-dlp")):
    """Extract video information and cache it with hash based on platform and video ID."""
    try:
        video_info = extract_video_info(url)
        return video_info
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/cache/info/{video_id}",
    response_model=VideoInfo,
    summary="Get Cached Video Information",
    tags=["Cache Operations"]
)
async def get_video_info(video_id: str = Path(..., description="Hash of cached video")):
    """Retrieve cached video information by hash."""

    video_cache = CacheRegistry.get_default()
    if not video_cache.exists(video_id):
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    cached: dict = video_cache[video_id]  # type: ignore
    return VideoInfo(**cached)

@app.get(
    "/api/download",
    summary="Download Video from URL",
    tags=["Video Operations"],
    responses={
        200: {
            "description": "Video file downloaded successfully. Video hash included in X-Video-Hash header.",
            "content": {"video/mp4": {}}
        },
        400: {"description": "Invalid or unsupported URL provided"},
        500: {"description": "Server error during video extraction or download"}
    }
)
async def download(url: str = Query(..., description="Video URL from any platform supported by yt-dlp")):
    """Download video and return file. Video info is automatically extracted and cached."""
    try:

        options = get_default_download_options(url)

        # Download video
        output_file = download_video(options)

        # Return file with hash in response header
        return FileResponse(
            output_file,
            media_type='video/mp4',
            headers={"Content-Disposition": f"attachment; filename={output_file}"}
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/cache/download/{video_id}",
    summary="Download Video by Hash",
    tags=["Cache Operations"],
    responses={
        200: {
            "description": "Cached video file downloaded successfully as binary stream",
            "content": {"application/octet-stream": {}}
        },
        404: {"description": "Video hash not found in cache or URL missing from cache entry"},
        500: {"description": "Server error during video download"}
    }
)
async def download_by_id(video_id: str = Path(..., description="Hash of cached video")):
    """Download video file using cached hash. The video info must have been previously extracted."""
    video_cache = CacheRegistry.get_default()
    if video_cache.exists(video_id):
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    video_info: VideoCacheData = video_cache.get(video_id) # type: ignore
    file_path = video_info.get("output_path")

    if file_path and os.path.exists(file_path):
        return FileResponse(file_path, media_type='application/octect-stream', headers={"Content-Disposition": f"attachment; filename={file_path}"})
    else:
        try:
            options = video_info["download_options"] or get_default_download_options(video_info["url"])
            file_path = download_video(options)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to download video: {str(e)}")
        
    return FileResponse(
        file_path,
        media_type='application/octect-stream',
        headers={"Content-Disposition": f"attachment; filename={file_path}"}
    )


@app.post(
    "/api/download/advanced",
    summary="Advanced Video Download",
    tags=["Video Operations"],
    responses={
        200: {
            "description": "Video downloaded with specified options as binary stream. Response headers include X-Video-Hash, X-Quality, and X-Format.",
            "content": {"application/octet-stream": {}}
        },
        400: {"description": "Invalid URL or invalid download parameters"},
        500: {"description": "Server error during advanced video processing"}
    }
)
async def download_advanced(request: VideoDownloadOptions):
    """Download video with advanced options for format, quality, and codec specifications."""
    try:

        # Download video with advanced options
        output_file = download_video(request)

        # Return file with hash in response header
        return FileResponse(
            output_file,
            media_type='application/octet-stream',
            headers={
                "Content-Disposition": f"attachment; filename={output_file}",
                "X-Quality": request.quality,
                "X-Format": request.file_format,
            }
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/cache",
    summary="Get Cache Status",
    tags=["Cache Operations"],
    responses={
        200: {
            "description": "Cache status retrieved successfully with list of all cached videos",
            "content": {
                "application/json": {
                    "example": {
                        "cached_videos": {
                            "youtube_123456789": {
                                    "video_hash": "abc123def456",
                                    "title": "Example Video",
                                    "platform": "youtube",
                                    "video_id": "dQw4w9WgXcQ",
                                    "age_seconds": 125.5,
                                    "expires_in_seconds": 3474.5
                                }
                            }
                        },
                        "count": 1
                    }
                }
            }
        }
)
async def get_cache_status():
    """View all cached videos and their expiration status."""
    video_cache = CacheRegistry.get_default()

    cache_dict = {}
    for (key, value) in video_cache.items():
        cache_dict[key] = {
            "id": value.get("id"),
            "url": value.get("url"),
            "output_path": value.get("output_path"),
            "info": value.get("info"),
            "raw_info": value.get("raw_info")
        }

    return {
        "cached_videos": cache_dict,
        "count": video_cache.size()
    }

@app.delete(
    "/api/cache/{video_hash}",
    summary="Delete Cached Video",
    tags=["Cache Operations"],
    responses={
        200: {
            "description": "Cache entry deleted successfully",
            "content": {
                "application/json": {
                    "example": {"message": "Cache cleared for hash: abc123def456"}
                }
            }
        },
        404: {"description": "Video hash not found in cache"}
    }
)
async def clear_cache(video_hash: str = Path(..., description="Hash of video to delete")):
    """Delete cached video information."""
    video_cache = CacheRegistry.get_default()
    if video_cache.exists(video_hash):
        raise HTTPException(status_code=404, detail="Video hash not found in cache")
    
    #del video_cache[video_hash]
    return {"message": f"Cache cleared for hash: {video_hash}"}
