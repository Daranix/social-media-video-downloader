import os
import time
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.responses import FileResponse

from .config import TEMP_DIR
from .cache import video_cache, cleanup_expired_cache
from .models import VideoInfo, VideoDownloadRequest
from .utils import is_url_valid
from .ytdl_ops import extract_video_info, download_video, download_video_advanced


app = FastAPI(
    title="Video Downloader API",
    description="Download videos from any platform supported by yt-dlp with automatic caching and metadata extraction",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)


@app.get(
    "/api/extract",
    response_model=VideoInfo,
    summary="Extract Video Information",
    tags=["Video Operations"],
    responses={
        200: {
            "description": "Video metadata extracted and cached successfully",
            "content": {
                "application/json": {
                    "example": {
                        "video_hash": "abc123def456",
                        "url": "https://example.com/video",
                        "title": "Example Video",
                        "duration": 3600,
                        "uploader": "Example Channel",
                        "platform": "youtube",
                        "video_id": "dQw4w9WgXcQ",
                        "view_count": 1000000,
                        "thumbnail": "https://example.com/thumb.jpg"
                    }
                }
            }
        },
        400: {"description": "Invalid or unsupported URL"},
        500: {"description": "Server error during metadata extraction"}
    }
)
async def extract_info(url: str = Query(..., description="Video URL from any platform supported by yt-dlp")):
    """Extract video information and cache it with hash based on platform and video ID."""
    try:
        # Cleanup expired cache before adding new entry
        cleanup_expired_cache()

        # Validate URL
        if not is_url_valid(url):
            raise HTTPException(status_code=400, detail="URL is not valid or not supported by yt-dlp")

        video_info = extract_video_info(url)
        video_hash = video_info["video_hash"]

        # Store in cache with timestamp (info only, no file)
        video_cache[video_hash] = {
            **video_info,
            "timestamp": time.time(),
        }

        return VideoInfo(**video_info)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/info/{video_hash}",
    response_model=VideoInfo,
    summary="Get Cached Video Information",
    tags=["Cache Operations"],
    responses={
        200: {
            "description": "Cached video metadata retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "video_hash": "abc123def456",
                        "url": "https://example.com/video",
                        "title": "Example Video",
                        "platform": "youtube"
                    }
                }
            }
        },
        404: {"description": "Video hash not found in cache"}
    }
)
async def get_video_info(video_hash: str = Path(..., description="Hash of cached video")):
    """Retrieve cached video information by hash."""
    if video_hash not in video_cache:
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    cached: dict = video_cache[video_hash]  # type: ignore
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
        # Validate URL
        if not is_url_valid(url):
            raise HTTPException(status_code=400, detail="URL is not valid or not supported by yt-dlp")

        video_info = extract_video_info(url)
        video_hash = video_info["video_hash"]

        # Cache video info (not the file)
        video_cache[video_hash] = {
            **video_info,
            "timestamp": time.time(),
        }

        # Create temporary file path for download
        file_name = f"{video_hash}.mp4"
        file_path = os.path.join(TEMP_DIR, file_name)

        # Download video
        actual_file = download_video(url, file_path)

        # Return file with hash in response header
        return FileResponse(
            actual_file,
            media_type='video/mp4',
            headers={"X-Video-Hash": video_hash, "Content-Disposition": f"attachment; filename={file_name}"}
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get(
    "/api/download/{video_hash}",
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
async def download_by_hash(video_hash: str = Path(..., description="Hash of cached video")):
    """Download video file using cached hash. The video info must have been previously extracted."""
    if video_hash not in video_cache:
        raise HTTPException(status_code=404, detail="Video hash not found in cache. Please extract info first.")

    cached: dict = video_cache[video_hash]  # type: ignore
    url = cached["url"] if "url" in cached else None

    if not url:
        raise HTTPException(status_code=404, detail="Video URL not found in cache")

    # Download video on demand
    file_name = f"{cached.get('title', 'video')}.mp4"
    file_path = os.path.join(TEMP_DIR, file_name)

    try:
        actual_file = download_video(url, file_path)
        return FileResponse(
            actual_file,
            media_type='video/mp4',
            headers={"Content-Disposition": f"attachment; filename={file_name}"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download video: {str(e)}")


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
async def download_advanced(request: VideoDownloadRequest):
    """Download video with advanced options for format, quality, and codec specifications."""
    try:
        # Cleanup expired cache before adding new entry
        cleanup_expired_cache()

        # Validate URL
        if not is_url_valid(request.url):
            raise HTTPException(status_code=400, detail="URL is not valid or not supported by yt-dlp")

        video_info = extract_video_info(request.url)
        video_hash = video_info["video_hash"]

        # Cache video info only (not the file)
        video_cache[video_hash] = {
            **video_info,
            "timestamp": time.time(),
        }

        # Create temporary file path
        file_name = f"{video_hash}.{request.file_format}"
        file_path = os.path.join(TEMP_DIR, file_name)

        # Download video with advanced options
        actual_file = download_video_advanced(request.url, file_path, request)

        # Return file with hash in response header
        return FileResponse(
            actual_file,
            media_type='video/mp4' if request.file_format == 'mp4' else 'application/octet-stream',
            headers={
                "X-Video-Hash": video_hash,
                "Content-Disposition": f"attachment; filename={file_name}",
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
                        "cached_videos": [
                            {
                                "video_hash": "abc123def456",
                                "title": "Example Video",
                                "platform": "youtube",
                                "video_id": "dQw4w9WgXcQ",
                                "age_seconds": 125.5,
                                "expires_in_seconds": 3474.5
                            }
                        ],
                        "count": 1
                    }
                }
            }
        }
    }
)
async def get_cache_status():
    """View all cached videos and their expiration status."""
    cache_summary = []
    for video_hash in list(video_cache):
        info: dict = video_cache[video_hash]  # type: ignore
        timestamp = info.get("timestamp", time.time())
        age_seconds = time.time() - timestamp
        cache_ttl = int(os.getenv('CACHE_TTL_SECONDS', '3600'))
        expires_in = max(0, cache_ttl - age_seconds)

        cache_summary.append({
            "video_hash": video_hash,
            "title": info.get("title"),
            "platform": info.get("platform"),
            "video_id": info.get("video_id"),
            "url": info.get("url"),
            "age_seconds": round(age_seconds, 2),
            "expires_in_seconds": round(expires_in, 2)
        })
    return {
        "cached_videos": cache_summary,
        "count": len(cache_summary),
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
    if video_hash not in video_cache:
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    del video_cache[video_hash]
    return {"message": f"Cache cleared for hash: {video_hash}"}
