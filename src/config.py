import os
import tempfile
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Default configuration values
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 3600))  # Default: 1 hour
CACHE_CLEANUP_INTERVAL = int(os.getenv("CACHE_CLEANUP_INTERVAL", 300))  # Default: 5 minutes
TEMP_DIR = tempfile.gettempdir()
