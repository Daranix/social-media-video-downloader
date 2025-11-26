# Dependencies

Python packages (install via pip):

- fastapi
- uvicorn[standard]
- yt-dlp
- diskcache
- python-dotenv
- pydantic

System packages:

- ffmpeg (required for many post-processing tasks used by yt-dlp)

Example (pip):

```powershell
pip install fastapi "uvicorn[standard]" yt-dlp diskcache python-dotenv pydantic
```

On Debian/Ubuntu, install ffmpeg:

```bash
apt-get update && apt-get install -y ffmpeg
```
