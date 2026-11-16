import os
import stat
import time
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
import yt_dlp
from yt_dlp.cookies import CookieLoadError
from yt_dlp.networking.exceptions import (
    HTTPError,
    NoSupportingHandlers,
    ProxyError,
    RequestError,
    SSLError,
    TransportError,
    UnsupportedRequest,
)
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    LazyList,
    UnavailableVideoError,
    UnsupportedError,
)

from src import config, utils, ytdl_ops
from src.cache.cache_manager import CacheManager
from src.cache.cache_registry import CacheRegistry
from src.cache.in_memory_cache import InMemoryCache
from src.models.video_data import VideoDownloadOptions


def _make_info(extractor: str = "Instagram", video_id: str = "abc123") -> dict:
    return {
        "extractor": extractor,
        "id": video_id,
        "title": "Sample video",
        "duration": 12.5,
        "uploader": "tester",
        "thumbnail": "https://example.com/thumb.jpg",
        "description": "desc",
        "view_count": 100,
        "like_count": 10,
        "upload_date": "20261116",
    }


def _fake_http_error(status: int, reason: str = "Error") -> HTTPError:
    return HTTPError(SimpleNamespace(status=status, reason=reason, close=lambda: None))


class TestUrlValidationAndUtils:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "file:///etc/passwd",
            "ftp://example.com/video.mp4",
            "javascript:alert(1)",
            "https:///missing-host",
            "http://[bad/",
            "http://foo]bar/",
            "http://example.com:badport/video",
            "http://127.0.0.1\\foo.com",
            "http://localhost\\a.b",
            "https://www.youtube.com/watch\n?v=1",
            "https://www.youtube.com/watch\x00?v=1",
            "https://user:pass@www.youtube.com/watch?v=1",
            "http://localhost/reel/123",
            "https://sub.localhost/watch?v=1",
            "http://ip6-localhost/reel/123",
            "http://host.docker.internal/video",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://service.local/video",
            "http://service.internal/video",
            "http://redis/video",
            "http://127.0.0.1:8000/secret",
            "http://2130706433/secret",
            "http://0177.0.0.1/secret",
            "http://0x7f000001/secret",
            "http://127.1/secret",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/video",
            "http://172.16.0.1/video",
            "http://192.168.1.10/video",
            "http://100.64.0.1/video",
            "http://0.0.0.0/video",
            "http://[::1]/video",
            "http://[::ffff:127.0.0.1]/video",
            "http://[64:ff9b::127.0.0.1]/video",
            "http://[2002:7f00:0001::]/video",
            "http://[fe80::1]/video",
            "http://[ff02::1]/video",
        ],
    )
    def test_validate_media_url_rejects_disallowed_urls(self, bad_url):
        with pytest.raises(HTTPException) as exc_info:
            ytdl_ops.validate_media_url(bad_url)
        assert exc_info.value.status_code == 400

    def test_validate_media_url_accepts_public_http_and_https(self):
        assert (
            ytdl_ops.validate_media_url("  https://www.instagram.com/reel/Chunk8-jurw/  ")
            == "https://www.instagram.com/reel/Chunk8-jurw/"
        )

    def test_extract_video_id_handles_none_fields(self):
        vid = utils.extract_video_id({"extractor": None, "id": None})
        assert vid.startswith("unknown_")
        assert len(vid) > len("unknown_")

    def test_detect_platform_blocks_ssrf_and_handles_non_dict(self, monkeypatch):
        called = False

        def _unexpected(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("YoutubeDL should not be invoked for SSRF URL")

        monkeypatch.setattr(yt_dlp, "YoutubeDL", _unexpected)
        assert utils.detect_platform("http://127.0.0.1/admin") == "unknown"
        assert not called


class TestYtdlOptionsConstruction:
    def test_shared_options_require_explicit_cookiefile_and_include_resilience_defaults(self, monkeypatch):
        monkeypatch.setattr(config, "PROXY_URL", "http://proxy.local:8080")
        monkeypatch.setattr(config, "YTDLP_SLEEP_REQUESTS", 1.5)
        monkeypatch.setattr(config, "YTDLP_EXTRACTOR_RETRIES", 4)
        monkeypatch.setattr(config, "COOKIES_FILE", "/tmp/raw_cookies.txt")

        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        base_opts_no_cookie = ytdl_ops.build_base_ytdl_options(url, quiet=True)
        assert "cookiefile" not in base_opts_no_cookie

        base_opts = ytdl_ops.build_base_ytdl_options(url, quiet=True, cookiefile="/tmp/temp_cookies.txt")
        dl_opts = ytdl_ops.build_ytdl_options(
            "/tmp/out",
            VideoDownloadOptions(url=url),
            cookiefile="/tmp/temp_cookies.txt",
        )

        for opts in (base_opts, dl_opts):
            assert opts["proxy"] == "http://proxy.local:8080"
            assert opts["sleep_interval_requests"] == 1.5
            assert opts["extractor_retries"] == 4
            assert opts["retries"] == ytdl_ops.DEFAULT_DOWNLOAD_RETRIES
            assert opts["fragment_retries"] == ytdl_ops.DEFAULT_FRAGMENT_RETRIES
            assert opts["socket_timeout"] == ytdl_ops.DEFAULT_SOCKET_TIMEOUT
            assert opts["cachedir"] is False
            assert opts["noplaylist"] is True
            assert opts["cookiefile"] == "/tmp/temp_cookies.txt"
            assert opts["extractor_args"] == {
                "youtube": {"player_client": ["default", "ios", "android_vr"]}
            }
            assert "impersonate" not in opts

        assert base_opts["quiet"] is True
        assert base_opts["no_warnings"] is True
        assert dl_opts["format"] == "bv+ba/best"
        assert dl_opts["merge_output_format"] == "mp4"
        assert dl_opts["outtmpl"] == os.path.join("/tmp/out", "%(extractor)s_%(id)s.%(ext)s")

    def test_audio_only_options_omit_merge_output_format(self):
        dl_opts = ytdl_ops.build_ytdl_options(
            "/tmp/out",
            VideoDownloadOptions(url="https://www.youtube.com/watch?v=1", audio_only=True),
        )
        assert dl_opts["format"] == "bestaudio"
        assert "merge_output_format" not in dl_opts

    @pytest.mark.parametrize(
        "url,override,expected",
        [
            ("https://www.instagram.com/reel/Chunk8-jurw/", None, ImpersonateTarget()),
            ("https://www.instagram.com./reel/Chunk8-jurw/", "   ", ImpersonateTarget()),
            ("www.instagram.com/reel/Chunk8-jurw/", None, ImpersonateTarget()),
            ("https://instagr.am/p/abc/", "auto", ImpersonateTarget()),
            ("https://www.instagram.com/reel/Chunk8-jurw/", "off", None),
            ("https://www.instagram.com/reel/Chunk8-jurw/", "chrome", ImpersonateTarget.from_str("chrome")),
            ("https://www.instagram.com/reel/Chunk8-jurw/", "chrome:windows-10", ImpersonateTarget.from_str("chrome:windows-10")),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None, None),
            ("youtube.com/watch?v=dQw4w9WgXcQ", "chrome", None),
            ("youtube.com:443/watch?v=dQw4w9WgXcQ", "chrome", None),
            ("https://www.youtube.com./watch?v=dQw4w9WgXcQ", "chrome", None),
            ("https://youtu.be/dQw4w9WgXcQ", "auto", None),
            ("https://example.com/?next=instagram.com", None, None),
            ("https://www.tiktok.com/@user/video/1", None, None),
            ("https://www.tiktok.com/@user/video/1", "chrome", ImpersonateTarget.from_str("chrome")),
        ],
    )
    def test_impersonation_target_resolution(self, monkeypatch, url, override, expected):
        monkeypatch.setattr(config, "YTDLP_IMPERSONATE", override)
        opts = ytdl_ops.build_base_ytdl_options(url)
        assert opts.get("impersonate") == expected

    @pytest.mark.parametrize("bad_target", [":", "chrome:win:extra"])
    def test_invalid_impersonation_override_does_not_reflect_raw_value(self, monkeypatch, bad_target):
        monkeypatch.setattr(config, "YTDLP_IMPERSONATE", bad_target)
        with pytest.raises(ytdl_ops.YTDLPConfigurationError) as exc_info:
            ytdl_ops.build_base_ytdl_options("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert str(exc_info.value) == "Invalid YTDLP_IMPERSONATE target"

    def test_real_youtubedl_accepts_built_options_for_instagram_and_youtube(self):
        ig_opts = ytdl_ops.build_base_ytdl_options("https://www.instagram.com/reel/Chunk8-jurw/", quiet=True)
        yt_opts = ytdl_ops.build_base_ytdl_options("https://www.youtube.com/watch?v=dQw4w9WgXcQ", quiet=True)

        with yt_dlp.YoutubeDL(ig_opts) as ig_ydl:
            assert ig_ydl.params["impersonate"] == ImpersonateTarget()
            assert ig_ydl.params["retries"] == ytdl_ops.DEFAULT_DOWNLOAD_RETRIES
            assert ig_ydl.params["fragment_retries"] == ytdl_ops.DEFAULT_FRAGMENT_RETRIES
            assert ig_ydl.params["socket_timeout"] == ytdl_ops.DEFAULT_SOCKET_TIMEOUT
            assert ig_ydl.params["noplaylist"] is True
            assert ig_ydl.params["extractor_retries"] == 3

        with yt_dlp.YoutubeDL(yt_opts) as yt_ydl:
            assert yt_ydl.params.get("impersonate") is None
            assert yt_ydl.params["extractor_args"] == {
                "youtube": {"player_client": ["default", "ios", "android_vr"]}
            }


class TestCookieHandlingSanitizationAndCleanup:
    def test_missing_cookies_file_raises_config_error_and_cleans_download_temp_dir(
        self, tmp_path, monkeypatch
    ):
        missing = tmp_path / "does_not_exist.txt"
        monkeypatch.setattr(config, "COOKIES_FILE", str(missing))

        called = False

        def _unexpected_ydl(*args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("YoutubeDL should not be instantiated when COOKIES_FILE is missing")

        monkeypatch.setattr(yt_dlp, "YoutubeDL", _unexpected_ydl)

        with pytest.raises(HTTPException) as exc_info:
            ytdl_ops.extract_video_info("https://www.instagram.com/reel/123/")
        assert exc_info.value.status_code == 500
        assert "COOKIES_FILE" in exc_info.value.detail
        assert not called

        with pytest.raises(ytdl_ops.YTDLPConfigurationError, match="COOKIES_FILE"):
            ytdl_ops.download_video(VideoDownloadOptions(url="https://www.instagram.com/reel/123/"))
        assert not called
        assert list(tmp_path.glob("smvd_*")) == []

    def test_mkdtemp_oserror_raises_configuration_error(self, tmp_path, monkeypatch):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        monkeypatch.setattr(config, "COOKIES_FILE", str(cookies))

        def _fail_mkdtemp(*args, **kwargs):
            raise OSError("disk read-only")

        monkeypatch.setattr(ytdl_ops.tempfile, "mkdtemp", _fail_mkdtemp)
        with pytest.raises(ytdl_ops.YTDLPConfigurationError, match="Temporary directory is not accessible"):
            with ytdl_ops._prepared_cookiefile():
                pass

    def test_extract_video_info_copies_readonly_cookies_sanitizes_raw_info_and_cleans_up(
        self, tmp_path, monkeypatch
    ):
        source_cookies = tmp_path / "source_cookies.txt"
        original_content = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret123\n"
        source_cookies.write_text(original_content, encoding="utf-8")
        source_cookies.chmod(stat.S_IREAD)

        monkeypatch.setattr(config, "COOKIES_FILE", str(source_cookies))

        captured: dict = {}

        class FakeYoutubeDL:
            sanitize_info = staticmethod(yt_dlp.YoutubeDL.sanitize_info)

            def __init__(self, opts):
                captured["opts"] = dict(opts)
                self.cookiefile = opts.get("cookiefile")

            def __enter__(self):
                assert self.cookiefile is not None
                assert self.cookiefile != str(source_cookies)
                assert os.path.isfile(self.cookiefile)
                with open(self.cookiefile, encoding="utf-8") as fh:
                    captured["temp_cookie_content"] = fh.read()
                return self

            def __exit__(self, exc_type, exc, tb):
                with open(self.cookiefile, "w", encoding="utf-8") as fh:
                    fh.write("# mutated by yt-dlp\n")

            def extract_info(self, url, download=False):
                return {
                    **_make_info("Instagram", "reel123"),
                    "http_headers": {"Cookie": "sessionid=secret"},
                    "cookies": "secret",
                    "filepath": "/tmp/internal.mp4",
                    "comments": LazyList(iter([{"text": "hi", "http_headers": {"X-Secret": "1"}}])),
                    "formats": ({"url": "https://cdn.example/v.mp4", "http_headers": {"Cookie": "s"}},),
                }

        monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)

        try:
            result = ytdl_ops.extract_video_info("https://www.instagram.com/reel/reel123/")
        finally:
            source_cookies.chmod(stat.S_IWRITE | stat.S_IREAD)

        assert result["id"] == "Instagram_reel123"
        assert "http_headers" not in result["raw_info"]
        assert "cookies" not in result["raw_info"]
        assert "filepath" not in result["raw_info"]
        assert result["raw_info"]["comments"] == [{"text": "hi"}]
        assert "http_headers" not in result["raw_info"]["formats"][0]
        assert ytdl_ops._strip_sensitive_info_keys(
            ({"a": 1, "http_headers": {"k": "v"}},)
        ) == [{"a": 1}]
        assert captured["temp_cookie_content"] == original_content
        assert source_cookies.read_text(encoding="utf-8") == original_content
        assert list(tmp_path.glob("smvd_cookies_*")) == []
        assert CacheRegistry.get_default().get_ttl("Instagram_reel123") is not None

    def test_zero_ttl_skips_cache_storage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CACHE_TTL_SECONDS", 0)

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.out_dir = os.path.dirname(opts.get("outtmpl", ""))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                if download:
                    out_file = os.path.join(self.out_dir, "Instagram_reel_zero.mp4")
                    with open(out_file, "wb") as fh:
                        fh.write(b"bytes")
                    return {
                        **_make_info("Instagram", "reel_zero"),
                        "requested_downloads": [{"filepath": out_file}],
                    }
                return _make_info("Instagram", "reel_zero")

            def prepare_filename(self, info):
                return os.path.join(self.out_dir, "Instagram_reel_zero.mp4")

        monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
        ytdl_ops.extract_video_info("https://www.instagram.com/reel/reel_zero/")
        assert CacheRegistry.get_default().size() == 0

        out = ytdl_ops.download_video(VideoDownloadOptions(url="https://www.instagram.com/reel/reel_zero/"))
        assert os.path.isfile(out)
        assert CacheRegistry.get_default().size() == 0

    def test_cookie_temp_dir_cleaned_up_on_extract_failure(self, tmp_path, monkeypatch):
        source_cookies = tmp_path / "cookies.txt"
        source_cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        monkeypatch.setattr(config, "COOKIES_FILE", str(source_cookies))

        observed_cookiefiles = []

        class FailingYoutubeDL:
            def __init__(self, opts):
                observed_cookiefiles.append(opts["cookiefile"])

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                raise DownloadError("ERROR: [Instagram] reel123: Video unavailable")

        monkeypatch.setattr(yt_dlp, "YoutubeDL", FailingYoutubeDL)

        with pytest.raises(HTTPException) as exc_info:
            ytdl_ops.extract_video_info("https://www.instagram.com/reel/reel123/")
        assert exc_info.value.status_code == 404
        assert len(observed_cookiefiles) == 1
        assert not os.path.exists(observed_cookiefiles[0])
        assert list(tmp_path.glob("smvd_cookies_*")) == []

    def test_download_video_rejects_unconfined_filepath(self, tmp_path, monkeypatch):
        outside_file = tmp_path / "outside.mp4"
        outside_file.write_bytes(b"escape")

        class EscapingYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=True):
                return {
                    **_make_info("Instagram", "escape"),
                    "requested_downloads": [{"filepath": str(outside_file)}],
                }

            def prepare_filename(self, info):
                return str(outside_file)

        monkeypatch.setattr(yt_dlp, "YoutubeDL", EscapingYDL)
        with pytest.raises(ExtractorError, match="not produced"):
            ytdl_ops.download_video(VideoDownloadOptions(url="https://www.instagram.com/reel/escape/"))
        assert outside_file.exists()
        assert list(tmp_path.glob("smvd_*")) == []

    def test_download_video_rejects_multi_entry_playlist_and_missing_output(self, tmp_path, monkeypatch):
        class PlaylistYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=True):
                return {"_type": "playlist", "extractor": "Instagram", "id": "p1", "entries": [{"id": "1"}, {"id": "2"}]}

            def prepare_filename(self, info):
                return str(tmp_path / "none.NA")

        monkeypatch.setattr(yt_dlp, "YoutubeDL", PlaylistYDL)
        with pytest.raises(ExtractorError, match="Multi-entry playlists"):
            ytdl_ops.download_video(VideoDownloadOptions(url="https://www.instagram.com/p/p1/"))
        assert list(tmp_path.glob("smvd_*")) == []

        class MissingFileYDL:
            def __init__(self, opts):
                self.out_dir = os.path.dirname(opts["outtmpl"])

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=True):
                return _make_info("Instagram", "reel_missing")

            def prepare_filename(self, info):
                return os.path.join(self.out_dir, "missing.mp4")

        monkeypatch.setattr(yt_dlp, "YoutubeDL", MissingFileYDL)
        with pytest.raises(ExtractorError, match="not produced"):
            ytdl_ops.download_video(VideoDownloadOptions(url="https://www.instagram.com/reel/missing/"))
        assert list(tmp_path.glob("smvd_*")) == []

    def test_sweeper_respects_leases_and_retries_failed_removals(self, tmp_path, monkeypatch):
        leased_dir = tmp_path / "smvd_leased"
        leased_dir.mkdir()
        retry_dir = tmp_path / "smvd_retry"
        retry_dir.mkdir()

        ytdl_ops.acquire_temp_dir(str(leased_dir))
        ytdl_ops._schedule_cleanup(str(leased_dir), 1)
        ytdl_ops._schedule_cleanup(str(retry_dir), 1)

        future = time.monotonic() + 2.0
        fail_first = True
        orig_try_remove = ytdl_ops._try_remove_path

        def _flaky_remove(p):
            nonlocal fail_first
            if ytdl_ops._normalize_dir_key(p) == ytdl_ops._normalize_dir_key(str(retry_dir)) and fail_first:
                fail_first = False
                return False
            return orig_try_remove(p)

        monkeypatch.setattr(ytdl_ops, "_try_remove_path", _flaky_remove)

        # First sweep: leased_dir skipped (active lease), retry_dir fails once and stays queued
        ytdl_ops._sweep_once(now=future)
        assert leased_dir.exists()
        assert retry_dir.exists()
        assert ytdl_ops._normalize_dir_key(str(retry_dir)) in ytdl_ops._PENDING_CLEANUPS

        # Schedule immediate managed cleanup while still leased -> stays alive until release, then deletes immediately
        ytdl_ops.schedule_managed_dir_cleanup(str(leased_dir))
        assert leased_dir.exists()
        ytdl_ops.release_temp_dir(str(leased_dir))
        assert not leased_dir.exists()

        # Second sweep removes retry_dir once retry deadline elapses
        ytdl_ops._sweep_once(now=time.monotonic() + 10.0)
        assert not retry_dir.exists()

    def test_deleting_dirs_blocks_acquire_and_sweeper_ignores_leased_deadlines(self, tmp_path):
        deleting_dir = tmp_path / "smvd_deleting"
        deleting_dir.mkdir()
        key = ytdl_ops._normalize_dir_key(str(deleting_dir))

        with ytdl_ops._CLEANUP_LOCK:
            ytdl_ops._DELETING_DIRS.add(key)
        assert ytdl_ops.acquire_temp_dir(str(deleting_dir)) is False

        with ytdl_ops._CLEANUP_LOCK:
            ytdl_ops._DELETING_DIRS.discard(key)
        assert ytdl_ops.acquire_temp_dir(str(deleting_dir)) is True

        # Expired entry with an active lease should not make _next_sweeper_deadline_locked() <= now
        with ytdl_ops._CLEANUP_LOCK:
            ytdl_ops._PENDING_CLEANUPS[key] = time.monotonic() - 10.0
            next_deadline = ytdl_ops._next_sweeper_deadline_locked()
        assert next_deadline is None
        ytdl_ops.release_temp_dir(str(deleting_dir))
        assert not deleting_dir.exists()


class TestInMemoryCacheHardening:
    def test_cache_manager_wires_cleanup_interval_and_filters_expired(self, monkeypatch):
        monkeypatch.setattr(config, "CACHE_CLEANUP_INTERVAL", 0)
        mgr_zero = CacheManager("in-memory")
        assert mgr_zero._strategy._cleanup_interval == 1

        monkeypatch.setattr(config, "CACHE_CLEANUP_INTERVAL", 45)
        mgr = CacheManager("in-memory")
        assert mgr._strategy._cleanup_interval == 45

        cache = InMemoryCache(cleanup_interval=60, max_entries=2)
        cache.set("expired", {"v": 1}, ttl=0)
        assert cache.size() == 0
        assert cache.keys() == []
        assert cache.values() == []
        assert cache.items() == []
        assert cache.exists("expired") is False
        assert cache.get_ttl("expired") is None
        assert cache.pop("expired") is None
        assert cache.snapshot_entries() == []
        assert cache.delete("expired") is False

        cache.set("k1", {"v": 1}, ttl=60)
        cache.set("k2", {"v": 2}, ttl=60)
        # Overwriting an existing key should not evict the other active key
        cache.set("k1", {"v": 10}, ttl=60)
        assert set(cache.keys()) == {"k1", "k2"}
        cache.set("k3", {"v": 3}, ttl=60)
        assert cache.keys() == ["k1", "k3"]


class TestMapYtdlpError:
    def test_instagram_combined_rate_limit_login_redirect_maps_to_429(self):
        exc = ExtractorError(
            "The webpage request was redirected to the login page. "
            "You have exceeded the rate-limit for accessing posts anonymously. "
            "Use --cookies-from-browser or --cookies for the authentication.",
            expected=True,
        )
        wrapped = DownloadError("ERROR: [Instagram] abc: " + str(exc))
        mapped = ytdl_ops.map_ytdlp_error(wrapped)
        assert mapped.status_code == 429
        assert "rate-limit" in mapped.detail

    def test_geo_restricted_with_video_unavailable_maps_to_403(self):
        geo_err = GeoRestrictedError("Video unavailable. This video is not available in your country")
        wrapped = DownloadError(
            "ERROR: [youtube] xyz: Video unavailable. This video is not available in your country",
            exc_info=(GeoRestrictedError, geo_err, None),
        )
        mapped = ytdl_ops.map_ytdlp_error(wrapped)
        assert mapped.status_code == 403

    def test_no_supporting_handlers_traversal(self):
        fake_handler = SimpleNamespace(RH_NAME="Urllib")
        proxy_req = UnsupportedRequest('Unsupported proxy type: "https"', handler=fake_handler)
        no_handlers = NoSupportingHandlers([proxy_req], [])
        wrapped = DownloadError(
            "ERROR: Unable to handle request",
            exc_info=(NoSupportingHandlers, no_handlers, None),
        )
        assert ytdl_ops.map_ytdlp_error(wrapped).status_code == 500

        unexpected_proxy = NoSupportingHandlers([], [ProxyError("tunnel failed")])
        wrapped_unexpected = DownloadError(
            "ERROR: Unable to handle request",
            exc_info=(NoSupportingHandlers, unexpected_proxy, None),
        )
        assert ytdl_ops.map_ytdlp_error(wrapped_unexpected).status_code == 503

    @pytest.mark.parametrize(
        "exc,expected_status",
        [
            (ytdl_ops.YTDLPConfigurationError("Configured COOKIES_FILE missing"), 500),
            (CookieLoadError("bad cookie jar /etc/secret.txt"), 500),
            (DownloadError("Impersonate target 'bad' is not available"), 500),
            (DownloadError("Unsupported impersonate target: chrome-999"), 500),
            (DownloadError("The extractor specified to use impersonation, but no impersonate target is available"), 500),
            (DownloadError("none of these impersonate targets are available: chrome"), 500),
            (DownloadError("ERROR: ffmpeg not found. Please install or provide the path using --ffmpeg-location"), 500),
            (DownloadError("ERROR: ffprobe and ffmpeg not found"), 500),
            (UnsupportedError("https://example.com/not-found"), 400),
            (DownloadError("ERROR: Unsupported URL: https://bad.invalid"), 400),
            (DownloadError("ERROR: Requested format is not available"), 400),
            (DownloadError("ERROR: [Instagram] id: Multi-entry playlists are not supported for single-file download"), 400),
            (DownloadError("ERROR: HTTP Error 429: Too Many Requests"), 429),
            (DownloadError("ERROR: rate-limit", exc_info=(HTTPError, _fake_http_error(429), None)), 429),
            (UnavailableVideoError("removed"), 404),
            (DownloadError("ERROR: [youtube] xyz: Video unavailable"), 404),
            (DownloadError("ERROR: [Instagram] xyz: Page not found"), 404),
            (DownloadError("ERROR: HTTP Error 404: Not Found"), 404),
            (GeoRestrictedError("Geo restricted"), 403),
            (DownloadError("ERROR: HTTP Error 403: Forbidden"), 403),
            (DownloadError("ERROR: HTTP Error 407: Proxy Authentication Required"), 503),
            (ProxyError("proxy host not found"), 503),
            (DownloadError("ERROR: [Instagram] id: You need to log in to access this content"), 401),
            (DownloadError("ERROR: Sign in to confirm you're not a bot. Use --cookies"), 401),
            (
                DownloadError(
                    "ERROR: [Instagram] id: Instagram sent an empty media response. "
                    "Check if this post is accessible without being logged-in. Use --cookies..."
                ),
                503,
            ),
            (TransportError("timed out"), 503),
            (_fake_http_error(429, "Too Many Requests"), 429),
            (SSLError("handshake failed"), 503),
            (RequestError("generic request error"), 502),
            (DownloadError("ERROR: HTTP Error 503: Service Unavailable"), 503),
            (DownloadError("ERROR: HTTP Error 522: Origin Connection Time-out"), 503),
            (DownloadError("ERROR: HTTP Error 400: Bad Request"), 502),
            (ExtractorError("Unable to extract video url; key not found"), 502),
        ],
    )
    def test_error_classification_matrix(self, exc, expected_status):
        mapped = ytdl_ops.map_ytdlp_error(exc)
        assert mapped.status_code == expected_status
        assert mapped.detail

    def test_cookie_load_error_returns_static_detail(self):
        mapped = ytdl_ops.map_ytdlp_error(CookieLoadError("/secret/path/cookies.txt line 4 bad"))
        assert mapped.status_code == 500
        assert mapped.detail == "Failed to load configured COOKIES_FILE"

    @pytest.mark.parametrize(
        "exc",
        [
            RuntimeError("connection timeout talking to /var/secret/db"),
            OSError("file does not exist: /etc/passwd"),
            ValueError("internal rate-limit counter failed"),
        ],
    )
    def test_unexpected_non_ytdlp_exceptions_return_generic_500(self, exc):
        mapped = ytdl_ops.map_ytdlp_error(exc)
        assert mapped.status_code == 500
        assert mapped.detail == "Internal server error"

    def test_sanitization_strips_ansi_newlines_credentials_proxy_and_paths_with_spaces(
        self, tmp_path, monkeypatch
    ):
        spaced_temp = tmp_path / "my temp dir"
        spaced_temp.mkdir()
        cookies_path = str(spaced_temp / "prod_cookies.txt")
        proxy_secret = "http://alice:p@ssword!@proxy.internal:8080"

        monkeypatch.setattr(config, "TEMP_DIR", str(spaced_temp))
        monkeypatch.setattr(config, "COOKIES_FILE", cookies_path)
        monkeypatch.setattr(config, "PROXY_URL", proxy_secret)

        temp_cookie = str(spaced_temp / "smvd_cookies_abc123" / "cookies.txt")
        temp_work = str(spaced_temp / "smvd_work99" / "video.mp4")

        exc = ExtractorError(
            f"\x1b[0;31mERROR:\x1b[0m \x1b]0;title\x07Failed loading {cookies_path}\n"
            f"and {temp_cookie} and {temp_work}\r\n"
            f"via {proxy_secret} and https://bob:s@cret@other.proxy:3128/path"
        )
        mapped = ytdl_ops.map_ytdlp_error(exc)
        assert mapped.status_code == 502
        assert "\x1b" not in mapped.detail
        assert "\n" not in mapped.detail
        assert "\r" not in mapped.detail
        assert "alice" not in mapped.detail
        assert "p@ssword!" not in mapped.detail
        assert "bob" not in mapped.detail
        assert "s@cret" not in mapped.detail
        assert "<proxy_url>" in mapped.detail
        assert "https://***:***@other.proxy:3128/path" in mapped.detail
        assert str(spaced_temp) not in mapped.detail
        assert cookies_path not in mapped.detail
        assert "smvd_cookies_abc123" not in mapped.detail
        assert "smvd_work99" not in mapped.detail
        assert "<cookies_file>" in mapped.detail
        assert "<temp_dir>" in mapped.detail
