import asyncio
from contextlib import asynccontextmanager
import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.responses import FileResponse

from src.cache.cache_registry import CacheRegistry
from src.models.video_cache import VideoCacheData
from src.models.video_data import VideoDownloadOptions, VideoInfo

from . import config
from .utils import redact_url_userinfo
from .ytdl_ops import (
    acquire_temp_dir,
    cleanup_stale_temp_dirs,
    download_video,
    extract_video_info,
    finalize_claimed_delete,
    get_default_download_options,
    map_ytdlp_error,
    release_temp_dir,
    release_temp_dir_lease,
    schedule_managed_dir_cleanup,
)


CacheRegistry.create('default', 'in-memory')


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize concurrency controls and sweep leftover temp dirs on startup."""
    app.state.ytdlp_semaphore = asyncio.Semaphore(max(1, int(config.YTDLP_MAX_CONCURRENT_JOBS)))
    await asyncio.to_thread(cleanup_stale_temp_dirs)
    yield


app = FastAPI(
    title="Video Downloader API",
    description="Download videos from any platform supported by yt-dlp with automatic caching and metadata extraction",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


class CleanupFileResponse(FileResponse):
    """FileResponse holding a temp-dir lease while streaming and cleaning up in finally."""

    def __init__(
        self,
        *args,
        lease_dir: Optional[str] = None,
        lease_acquired: bool = False,
        cleanup_on_finish: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._lease_dir = lease_dir
        self._lease_acquired = lease_acquired
        self._cleanup_on_finish = cleanup_on_finish

    async def __call__(self, scope, receive, send) -> None:
        if self._lease_dir and not self._lease_acquired:
            if not acquire_temp_dir(self._lease_dir):
                raise RuntimeError("Temporary directory is no longer available")
            self._lease_acquired = True
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._lease_dir and self._lease_acquired:
                self._lease_acquired = False
                if release_temp_dir_lease(
                    self._lease_dir,
                    remove_now=self._cleanup_on_finish,
                ):
                    cleanup_task = asyncio.create_task(
                        asyncio.to_thread(finalize_claimed_delete, self._lease_dir)
                    )
                    cleanup_task.add_done_callback(
                        lambda t: t.exception() if not t.cancelled() else None
                    )
                    await asyncio.shield(cleanup_task)


def _should_cleanup_immediately() -> bool:
    return config.CACHE_TTL_SECONDS is not None and config.CACHE_TTL_SECONDS <= 0


async def _run_ytdlp(func, *args, cleanup_orphan_dir: bool = False):
    """Execute blocking yt-dlp operations under bounded concurrency and cancellation safety."""
    semaphore = getattr(app.state, "ytdlp_semaphore", None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, int(config.YTDLP_MAX_CONCURRENT_JOBS)))
        app.state.ytdlp_semaphore = semaphore

    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=30.0)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="Server busy; too many concurrent media requests",
        ) from exc

    cancelled = False
    orphan_cleanup_dispatched = False
    task = asyncio.create_task(asyncio.to_thread(func, *args))

    def _dispatch_orphan_cleanup(result) -> None:
        nonlocal orphan_cleanup_dispatched
        if orphan_cleanup_dispatched or not (isinstance(result, str) and result):
            return
        orphan_cleanup_dispatched = True
        cleanup_task = asyncio.create_task(
            asyncio.to_thread(schedule_managed_dir_cleanup, os.path.dirname(result))
        )
        cleanup_task.add_done_callback(
            lambda t: t.exception() if not t.cancelled() else None
        )

    def _on_done(completed: asyncio.Task) -> None:
        semaphore.release()
        if completed.cancelled():
            return
        if completed.exception() is not None:
            return
        if cancelled and cleanup_orphan_dir:
            _dispatch_orphan_cleanup(completed.result())

    task.add_done_callback(_on_done)
    try:
        return await asyncio.shield(task)
    except BaseException:
        cancelled = True
        if (
            cleanup_orphan_dir
            and not orphan_cleanup_dispatched
            and task.done()
            and not task.cancelled()
            and task.exception() is None
        ):
            _dispatch_orphan_cleanup(task.result())
        raise


def _build_file_response(
    file_path: str,
    *,
    media_type: str,
    extra_headers: Optional[dict[str, str]] = None,
    lease_dir: Optional[str] = None,
    lease_acquired: bool = False,
    cleanup_on_finish: bool = False,
) -> CleanupFileResponse:
    """Create a CleanupFileResponse exposing only basename and managing lease cleanup."""
    filename = os.path.basename(file_path)
    resolved_lease_dir = lease_dir if lease_dir is not None else os.path.dirname(file_path)
    return CleanupFileResponse(
        file_path,
        media_type=media_type,
        filename=filename,
        headers=extra_headers,
        lease_dir=resolved_lease_dir,
        lease_acquired=lease_acquired,
        cleanup_on_finish=cleanup_on_finish,
    )


def _build_leased_download_response(
    output_file: str,
    *,
    media_type: str,
    extra_headers: Optional[dict[str, str]] = None,
) -> CleanupFileResponse:
    """Acquire lease for a freshly downloaded file and build CleanupFileResponse safely."""
    cleanup_now = _should_cleanup_immediately()
    lease_dir = os.path.dirname(output_file)
    if not acquire_temp_dir(lease_dir):
        raise HTTPException(
            status_code=503,
            detail="Temporary file expired before streaming",
        )
    try:
        return _build_file_response(
            output_file,
            media_type=media_type,
            extra_headers=extra_headers,
            lease_dir=lease_dir,
            lease_acquired=True,
            cleanup_on_finish=cleanup_now,
        )
    except Exception:
        release_temp_dir(lease_dir, remove_now=cleanup_now)
        raise


@app.get(
    "/api/info",
    response_model=VideoCacheData,
    summary="Extract Video Information",
    tags=["Video Operations"]
)
async def extract_info(url: str = Query(..., description="Video URL from any platform supported by yt-dlp")):
    """Extract video information and cache it by extracted video ID."""
    try:
        video_info = await _run_ytdlp(extract_video_info, url)
        return video_info
    except HTTPException:
        raise
    except Exception as e:
        raise map_ytdlp_error(e) from e


@app.get(
    "/api/cache/info/{video_id}",
    response_model=VideoInfo,
    summary="Get Cached Video Information",
    tags=["Cache Operations"]
)
async def get_video_info(video_id: str = Path(..., description="ID of cached video")):
    """Retrieve cached video information by ID."""

    video_cache = CacheRegistry.get_default()
    cached = video_cache.get(video_id)
    if not isinstance(cached, dict) or cached.get("info") is None:
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    return cached["info"]


@app.get(
    "/api/download",
    summary="Download Video from URL",
    tags=["Video Operations"],
    responses={
        200: {
            "description": "Video file downloaded successfully.",
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
        output_file = await _run_ytdlp(
            download_video,
            options,
            cleanup_orphan_dir=_should_cleanup_immediately(),
        )
        return _build_leased_download_response(
            output_file,
            media_type="video/mp4",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise map_ytdlp_error(e) from e


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
    """Download video file using cached ID. The video info must have been previously extracted."""
    video_cache = CacheRegistry.get_default()
    video_info: Optional[VideoCacheData] = video_cache.get(video_id)  # type: ignore
    if not isinstance(video_info, dict) or not video_info.get("url"):
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    file_path = video_info.get("output_path")

    if isinstance(file_path, str) and file_path:
        lease_dir = os.path.dirname(file_path)
        if acquire_temp_dir(lease_dir):
            if os.path.isfile(file_path):
                try:
                    return _build_file_response(
                        file_path,
                        media_type="application/octet-stream",
                        lease_dir=lease_dir,
                        lease_acquired=True,
                    )
                except Exception:
                    release_temp_dir(lease_dir)
                    raise
            release_temp_dir(lease_dir)

    try:
        options = video_info.get("download_options") or get_default_download_options(video_info["url"])
        output_file = await _run_ytdlp(
            download_video,
            options,
            cleanup_orphan_dir=_should_cleanup_immediately(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise map_ytdlp_error(e) from e

    return _build_leased_download_response(
        output_file,
        media_type="application/octet-stream",
    )


@app.post(
    "/api/download/advanced",
    summary="Advanced Video Download",
    tags=["Video Operations"],
    responses={
        200: {
            "description": "Video downloaded with specified options as binary stream. Response headers include X-Quality and X-Format.",
            "content": {"application/octet-stream": {}}
        },
        400: {"description": "Invalid URL or invalid download parameters"},
        500: {"description": "Server error during advanced video processing"}
    }
)
async def download_advanced(request: VideoDownloadOptions):
    """Download video with advanced options for format, quality, and codec specifications."""
    try:
        output_file = await _run_ytdlp(
            download_video,
            request,
            cleanup_orphan_dir=_should_cleanup_immediately(),
        )
        return _build_leased_download_response(
            output_file,
            media_type="application/octet-stream",
            extra_headers={
                "X-Quality": request.quality,
                "X-Format": request.file_format,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise map_ytdlp_error(e) from e


@app.get(
    "/api/cache",
    summary="Get Cache Status",
    tags=["Cache Operations"],
    responses={
        200: {
            "description": "Cache status retrieved successfully with sanitized metadata summaries",
            "content": {
                "application/json": {
                    "example": {
                        "cached_videos": {
                            "youtube_dQw4w9WgXcQ": {
                                "id": "youtube_dQw4w9WgXcQ",
                                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                "info": {"id": "youtube_dQw4w9WgXcQ", "title": "Example Video"},
                                "age_seconds": 125.5,
                                "expires_in_seconds": 3474.5,
                            }
                        },
                        "count": 1,
                    }
                }
            },
        }
    },
)
async def get_cache_status():
    """View active cached videos and their expiration status without internal paths or raw headers."""
    video_cache = CacheRegistry.get_default()

    cache_dict = {}
    for key, value, age_seconds, expires_in_seconds in video_cache.snapshot_entries():
        if not isinstance(value, dict):
            continue
        cache_dict[key] = {
            "id": value.get("id"),
            "url": redact_url_userinfo(value.get("url")),
            "info": value.get("info"),
            "age_seconds": age_seconds,
            "expires_in_seconds": expires_in_seconds,
        }

    return {
        "cached_videos": cache_dict,
        "count": len(cache_dict),
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
    """Delete cached video information and clean up any associated temp directory."""
    video_cache = CacheRegistry.get_default()
    deleted = video_cache.pop(video_hash)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Video hash not found in cache")

    output_path = deleted.get("output_path") if isinstance(deleted, dict) else None
    if isinstance(output_path, str) and output_path:
        schedule_managed_dir_cleanup(os.path.dirname(output_path))

    return {"message": f"Cache cleared for hash: {video_hash}"}
