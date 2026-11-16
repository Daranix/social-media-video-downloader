import asyncio
import threading
from starlette.responses import FileResponse
import pytest
import yt_dlp
from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError, UnsupportedError

from src import api, config, ytdl_ops
from src.cache.cache_registry import CacheRegistry
from src.models.video_data import VideoInfo


@pytest.fixture
def client():
    with TestClient(api.app) as test_client:
        yield test_client


class TestLifespanAndValidation:
    def test_startup_sweeps_stale_smvd_temp_dirs(self, tmp_path):
        stale_work = tmp_path / "smvd_leftover123"
        stale_work.mkdir()
        (stale_work / "stale.mp4").write_bytes(b"old")
        stale_cookies = tmp_path / "smvd_cookies_leftover"
        stale_cookies.mkdir()
        (stale_cookies / "cookies.txt").write_text("old", encoding="utf-8")

        with TestClient(api.app):
            assert not stale_work.exists()
            assert not stale_cookies.exists()

    def test_download_advanced_rejects_header_injection_with_422(self, client, monkeypatch):
        called = False

        def _unexpected(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("download_video should not be called for invalid headers")

        monkeypatch.setattr(api, "download_video", _unexpected)

        resp_crlf = client.post(
            "/api/download/advanced",
            json={
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "quality": "high\r\nX-Injected: 1",
                "file_format": "mp4",
            },
        )
        assert resp_crlf.status_code == 422
        assert not called

        resp_non_latin1 = client.post(
            "/api/download/advanced",
            json={
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "quality": "high",
                "file_format": "mp4\u0100",
            },
        )
        assert resp_non_latin1.status_code == 422
        assert not called


class TestInfoEndpoint:
    def test_info_success(self, client, monkeypatch):
        info = VideoInfo(id="Instagram_reel1", url="https://www.instagram.com/reel/reel1/", title="Test")
        monkeypatch.setattr(
            api,
            "extract_video_info",
            lambda url: {
                "id": "Instagram_reel1",
                "url": url,
                "output_path": None,
                "info": info,
                "raw_info": {"id": "reel1"},
                "download_options": None,
            },
        )

        resp = client.get("/api/info", params={"url": "https://www.instagram.com/reel/reel1/"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "Instagram_reel1"

    def test_info_rejects_local_or_non_http_url_with_400(self, client):
        for bad_url in (
            "file:///etc/passwd",
            "http://[bad/",
            "http://foo]bar/",
            "http://example.com:badport/video",
            "http://127.0.0.1\\foo.com",
            "http://localhost\\a.b",
            "http://127.0.0.1/admin",
            "http://2130706433/admin",
            "http://169.254.169.254/latest",
            "https://user:pass@youtube.com/watch?v=1",
        ):
            resp = client.get("/api/info", params={"url": bad_url})
            assert resp.status_code == 400

    @pytest.mark.parametrize(
        "exc,expected_status",
        [
            (DownloadError("ERROR: [Instagram] x: You have exceeded the rate-limit"), 429),
            (DownloadError("ERROR: [Instagram] x: You need to log in to access this content"), 401),
            (UnsupportedError("https://example.com/nope"), 400),
            (DownloadError("ERROR: Video unavailable"), 404),
            (ytdl_ops.YTDLPConfigurationError("Configured COOKIES_FILE does not exist"), 500),
        ],
    )
    def test_info_maps_ytdlp_and_config_errors(self, client, monkeypatch, exc, expected_status):
        def _fake_extract(url):
            raise ytdl_ops.map_ytdlp_error(exc)

        monkeypatch.setattr(api, "extract_video_info", _fake_extract)
        resp = client.get("/api/info", params={"url": "https://www.instagram.com/reel/x/"})
        assert resp.status_code == expected_status
        assert resp.json()["detail"]

    def test_info_end_to_end_with_mocked_youtubedl(self, client, monkeypatch):
        class RateLimitedYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                raise DownloadError(
                    "ERROR: [Instagram] reel1: The webpage request was redirected to the login page. "
                    "You have exceeded the rate-limit for accessing posts anonymously."
                )

        monkeypatch.setattr(yt_dlp, "YoutubeDL", RateLimitedYDL)
        resp = client.get("/api/info", params={"url": "https://www.instagram.com/reel/reel1/"})
        assert resp.status_code == 429
        assert "rate-limit" in resp.json()["detail"]

    def test_info_end_to_end_missing_cookies_file_returns_500(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "COOKIES_FILE", str(tmp_path / "missing.txt"))
        resp = client.get("/api/info", params={"url": "https://www.instagram.com/reel/reel1/"})
        assert resp.status_code == 500
        assert "COOKIES_FILE" in resp.json()["detail"]


class TestDownloadAndCacheEndpoints:
    def test_download_success_basename_header_and_error_mapping(self, client, tmp_path, monkeypatch):
        work_dir = tmp_path / "smvd_test1"
        work_dir.mkdir()
        sample_file = work_dir / "Instagram_reel1.mp4"
        sample_file.write_bytes(b"mp4-content")

        monkeypatch.setattr(api, "download_video", lambda opts: str(sample_file))
        ok_resp = client.get("/api/download", params={"url": "https://www.instagram.com/reel/reel1/"})
        assert ok_resp.status_code == 200
        assert ok_resp.content == b"mp4-content"
        assert ok_resp.headers["content-type"].startswith("video/mp4")
        cd = ok_resp.headers["content-disposition"]
        assert "Instagram_reel1.mp4" in cd
        assert str(work_dir) not in cd

        def _raise_rate_limit(opts):
            raise DownloadError(
                "ERROR: [Instagram] reel1: The webpage request was redirected to the login page. "
                "You have exceeded the rate-limit for accessing posts anonymously."
            )

        monkeypatch.setattr(api, "download_video", _raise_rate_limit)
        err_resp = client.get("/api/download", params={"url": "https://www.instagram.com/reel/reel1/"})
        assert err_resp.status_code == 429
        assert "rate-limit" in err_resp.json()["detail"]

    def test_download_zero_ttl_cleans_up_temp_dir_and_handles_stream_exception(
        self, client, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(config, "CACHE_TTL_SECONDS", 0)
        work_dir = tmp_path / "smvd_zero_ttl"
        work_dir.mkdir()
        sample_file = work_dir / "Instagram_reel_ttl0.mp4"
        sample_file.write_bytes(b"streamed-bytes")

        monkeypatch.setattr(api, "download_video", lambda opts: str(sample_file))
        resp = client.get("/api/download", params={"url": "https://www.instagram.com/reel/reel1/"})
        assert resp.status_code == 200
        assert resp.content == b"streamed-bytes"
        assert not work_dir.exists()

        # Verify CleanupFileResponse also cleans up in finally if streaming raises
        err_dir = tmp_path / "smvd_stream_err"
        err_dir.mkdir()
        err_file = err_dir / "fail.mp4"
        err_file.write_bytes(b"fail")

        async def _boom(self, scope, receive, send):
            raise RuntimeError("client disconnected")

        monkeypatch.setattr(FileResponse, "__call__", _boom)
        cleanup_resp = api.CleanupFileResponse(
            str(err_file),
            lease_dir=str(err_dir),
            cleanup_on_finish=True,
        )

        async def _noop(*args, **kwargs):
            return None

        with pytest.raises(RuntimeError, match="client disconnected"):
            asyncio.run(cleanup_resp({"type": "http"}, _noop, _noop))
        assert not err_dir.exists()

    def test_download_advanced_success_and_error_mapping(self, client, tmp_path, monkeypatch):
        work_dir = tmp_path / "smvd_adv"
        work_dir.mkdir()
        sample_file = work_dir / "YouTube_vid1.mp4"
        sample_file.write_bytes(b"adv-mp4")

        monkeypatch.setattr(api, "download_video", lambda opts: str(sample_file))
        ok_resp = client.post(
            "/api/download/advanced",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "quality": "high", "file_format": "mp4"},
        )
        assert ok_resp.status_code == 200
        assert ok_resp.content == b"adv-mp4"
        assert ok_resp.headers["content-type"] == "application/octet-stream"
        assert "YouTube_vid1.mp4" in ok_resp.headers["content-disposition"]
        assert str(work_dir) not in ok_resp.headers["content-disposition"]

        def _raise_unavailable(opts):
            raise DownloadError("ERROR: Video unavailable")

        monkeypatch.setattr(api, "download_video", _raise_unavailable)
        err_resp = client.post(
            "/api/download/advanced",
            json={"url": "https://www.youtube.com/watch?v=missing"},
        )
        assert err_resp.status_code == 404
        assert "Video unavailable" in err_resp.json()["detail"]

    def test_cache_status_sanitized_and_delete_cleans_managed_dir(self, client, tmp_path, monkeypatch):
        assert client.get("/api/cache/info/nonexistent").status_code == 404
        assert client.get("/api/cache/download/nonexistent").status_code == 404
        assert client.delete("/api/cache/nonexistent").status_code == 404

        cached_dir = tmp_path / "smvd_cached_1"
        cached_dir.mkdir()
        cached_file = cached_dir / "cached.mp4"
        cached_file.write_bytes(b"cached-video")

        cache = CacheRegistry.get_default()
        video_info = VideoInfo(
            id="Instagram_reel1",
            url="https://www.instagram.com/reel/reel1/",
            title="Cached Reel",
            platform="Instagram",
            video_id="Instagram_reel1",
        )
        cache.set(
            "Instagram_reel1",
            {
                "id": "Instagram_reel1",
                "url": "https://user:secret@www.instagram.com/reel/reel1/",
                "output_path": str(cached_file),
                "info": video_info,
                "raw_info": {"http_headers": {"Authorization": "Bearer secret"}},
                "download_options": None,
            },
            ttl=60,
        )

        status_resp = client.get("/api/cache")
        assert status_resp.status_code == 200
        payload = status_resp.json()
        assert payload["count"] == 1
        entry = payload["cached_videos"]["Instagram_reel1"]
        assert "output_path" not in entry
        assert "raw_info" not in entry
        assert "secret" not in entry["url"]
        assert entry["age_seconds"] is not None
        assert entry["expires_in_seconds"] is not None

        info_resp = client.get("/api/cache/info/Instagram_reel1")
        assert info_resp.status_code == 200
        assert info_resp.json()["title"] == "Cached Reel"

        # Verify download_by_id holds a lease during FileResponse streaming so sweeper cannot delete mid-stream
        orig_call = FileResponse.__call__

        async def _assert_leased_during_stream(self, scope, receive, send):
            ytdl_ops._schedule_cleanup(str(cached_dir), 1)
            ytdl_ops._sweep_once(now=10**12)
            assert cached_dir.exists()
            return await orig_call(self, scope, receive, send)

        monkeypatch.setattr(FileResponse, "__call__", _assert_leased_during_stream)
        hit_resp = client.get("/api/cache/download/Instagram_reel1")
        assert hit_resp.status_code == 200
        assert hit_resp.content == b"cached-video"
        assert hit_resp.headers["content-type"] == "application/octet-stream"
        assert "cached.mp4" in hit_resp.headers["content-disposition"]
        assert str(cached_dir) not in hit_resp.headers["content-disposition"]

        # DELETE removes cache entry and its unleased smvd_* temp directory
        del_resp = client.delete("/api/cache/Instagram_reel1")
        assert del_resp.status_code == 200
        assert not cached_dir.exists()
        assert client.get("/api/cache/info/Instagram_reel1").status_code == 404
        assert client.delete("/api/cache/Instagram_reel1").status_code == 404

    def test_cache_download_fallback_maps_errors(self, client, monkeypatch):
        cache = CacheRegistry.get_default()
        cache.set(
            "Instagram_reel2",
            {
                "id": "Instagram_reel2",
                "url": "https://www.instagram.com/reel/reel2/",
                "output_path": None,
                "info": VideoInfo(id="Instagram_reel2", url="https://www.instagram.com/reel/reel2/"),
                "raw_info": {},
                "download_options": None,
            },
            ttl=60,
        )

        def _raise_blocked(opts):
            raise DownloadError("ERROR: [Instagram] reel2: Instagram sent an empty media response")

        monkeypatch.setattr(api, "download_video", _raise_blocked)
        err_resp = client.get("/api/cache/download/Instagram_reel2")
        assert err_resp.status_code == 503
        assert "empty media response" in err_resp.json()["detail"]

    def test_cache_download_falls_back_when_file_missing_or_deleting(self, client, tmp_path, monkeypatch):
        stale_dir = tmp_path / "smvd_stale_cache"
        stale_dir.mkdir()
        missing_file = stale_dir / "gone.mp4"
        present_dir = tmp_path / "smvd_deleting_cache"
        present_dir.mkdir()
        deleting_file = present_dir / "deleting.mp4"
        deleting_file.write_bytes(b"old-bytes")

        fresh_dir = tmp_path / "smvd_fresh"
        fresh_dir.mkdir()
        fresh_file = fresh_dir / "fresh.mp4"
        fresh_file.write_bytes(b"fresh-video")

        cache = CacheRegistry.get_default()
        cache.set(
            "Instagram_missing_file",
            {
                "id": "Instagram_missing_file",
                "url": "https://www.instagram.com/reel/missing/",
                "output_path": str(missing_file),
                "info": VideoInfo(id="Instagram_missing_file", url="https://www.instagram.com/reel/missing/"),
                "raw_info": {},
                "download_options": None,
            },
            ttl=60,
        )
        cache.set(
            "Instagram_deleting",
            {
                "id": "Instagram_deleting",
                "url": "https://www.instagram.com/reel/deleting/",
                "output_path": str(deleting_file),
                "info": VideoInfo(id="Instagram_deleting", url="https://www.instagram.com/reel/deleting/"),
                "raw_info": {},
                "download_options": None,
            },
            ttl=60,
        )

        fallback_calls = []

        def _fake_download(opts):
            fallback_calls.append(opts.url)
            return str(fresh_file)

        monkeypatch.setattr(api, "download_video", _fake_download)

        # Case 1: output_path is missing on disk -> falls back to download_video
        resp1 = client.get("/api/cache/download/Instagram_missing_file")
        assert resp1.status_code == 200
        assert resp1.content == b"fresh-video"

        # Case 2: acquire_temp_dir returns False (directory being deleted) -> falls back to download_video
        key = ytdl_ops._normalize_dir_key(str(present_dir))
        with ytdl_ops._CLEANUP_LOCK:
            ytdl_ops._DELETING_DIRS.add(key)
        try:
            resp2 = client.get("/api/cache/download/Instagram_deleting")
        finally:
            with ytdl_ops._CLEANUP_LOCK:
                ytdl_ops._DELETING_DIRS.discard(key)
        assert resp2.status_code == 200
        assert resp2.content == b"fresh-video"
        assert fallback_calls == [
            "https://www.instagram.com/reel/missing/",
            "https://www.instagram.com/reel/deleting/",
        ]

    def test_cancelled_run_ytdlp_holds_semaphore_and_cleans_orphan_when_ttl_zero(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(config, "CACHE_TTL_SECONDS", 0)
        work_dir = tmp_path / "smvd_orphan_cancel"
        work_dir.mkdir()
        out_file = work_dir / "video.mp4"

        started = threading.Event()
        proceed = threading.Event()

        def _slow_download(arg):
            started.set()
            assert proceed.wait(timeout=5.0)
            out_file.write_bytes(b"orphan-bytes")
            return str(out_file)

        async def _scenario():
            sem = asyncio.Semaphore(1)
            api.app.state.ytdlp_semaphore = sem

            task = asyncio.create_task(
                api._run_ytdlp(_slow_download, "dummy", cleanup_orphan_dir=True)
            )
            while not started.is_set():
                await asyncio.sleep(0.01)

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # Semaphore must remain acquired while background thread is still running
            assert sem.locked() is True
            proceed.set()

            for _ in range(200):
                if not sem.locked() and not work_dir.exists():
                    break
                await asyncio.sleep(0.01)

            assert sem.locked() is False
            assert not work_dir.exists()

        asyncio.run(_scenario())

    def test_build_leased_download_response_503_when_deleting_and_cleanup_race_on_cancel(
        self, tmp_path, monkeypatch
    ):
        # 1. _build_leased_download_response raises 503 if acquire_temp_dir returns False
        deleting_dir = tmp_path / "smvd_race_del"
        deleting_dir.mkdir()
        target_file = deleting_dir / "video.mp4"
        target_file.write_bytes(b"data")
        key = ytdl_ops._normalize_dir_key(str(deleting_dir))

        with ytdl_ops._CLEANUP_LOCK:
            ytdl_ops._DELETING_DIRS.add(key)
        try:
            with pytest.raises(api.HTTPException) as exc_info:
                api._build_leased_download_response(str(target_file), media_type="video/mp4")
            assert exc_info.value.status_code == 503

            resp = api.CleanupFileResponse(str(target_file), lease_dir=str(deleting_dir))

            async def _noop(*args, **kwargs):
                return None

            with pytest.raises(RuntimeError, match="no longer available"):
                asyncio.run(resp({"type": "http"}, _noop, _noop))
        finally:
            with ytdl_ops._CLEANUP_LOCK:
                ytdl_ops._DELETING_DIRS.discard(key)

        # 2. _run_ytdlp cancelled AFTER inner task completed still cleans up orphan dir
        orphan_dir = tmp_path / "smvd_done_before_cancel"
        orphan_dir.mkdir()
        orphan_file = orphan_dir / "video.mp4"

        def _fast_download(arg):
            orphan_file.write_bytes(b"done-bytes")
            return str(orphan_file)

        orig_shield = asyncio.shield

        async def _shield_then_cancel(inner_task):
            await orig_shield(inner_task)
            raise asyncio.CancelledError()

        monkeypatch.setattr(api.asyncio, "shield", _shield_then_cancel)

        async def _race_scenario():
            api.app.state.ytdlp_semaphore = asyncio.Semaphore(1)
            with pytest.raises(asyncio.CancelledError):
                await api._run_ytdlp(_fast_download, "dummy", cleanup_orphan_dir=True)

            for _ in range(100):
                if not orphan_dir.exists():
                    break
                await asyncio.sleep(0.01)

            assert not orphan_dir.exists()
            assert not api.app.state.ytdlp_semaphore.locked()

        asyncio.run(_race_scenario())
