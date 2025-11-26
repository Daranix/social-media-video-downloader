# Social Media Video Downloader

Small FastAPI service that uses `yt-dlp` to extract metadata and download videos from platforms supported by `yt-dlp`. Downloads are cached (metadata only) and files are written to the system temporary directory when requested.

Quick start (local):

1. Install dependencies (see `DEPENDENCIES.md`).

```powershell
pip install fastapi "uvicorn[standard]" yt-dlp diskcache python-dotenv pydantic
```

2. Run the app:

```powershell
python main.py
# or
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

3. Open docs at: `http://localhost:8000/docs`

Docker (build & run):

```powershell
docker build -t sm-video-downloader .
docker run -p 8000:8000 sm-video-downloader
```

Files moved/created:

- `src/api.py` — FastAPI application and endpoints
- `src/ytdl_ops.py` — yt-dlp operations (extract/download)
- `src/utils.py` — helper functions (validation/hash/platform)
- `src/cache.py` — cache initialization and cleanup
- `src/models.py` — Pydantic models
- `src/config.py` — configuration defaults and environment loading
- `main.py` — small runner that starts the app
- `Dockerfile`, `DEPENDENCIES.md`, `README.md`

Notes:
- `ffmpeg` is required for many yt-dlp post-processing options (audio extraction, format conversions). Ensure it's installed on the host or in the container.
- The cache is in-memory by default. To persist between restarts, replace `Cache(None)` in `cache.py` with `Cache('/path/to/cache')`.
