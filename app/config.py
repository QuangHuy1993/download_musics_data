from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parents[1]
    BUNDLE_ROOT = ROOT

STATIC_DIR = BUNDLE_ROOT / "static"
DATA_DIR = Path(os.environ.get("MELON_DATA_DIR", ROOT / "data")).expanduser()
DEFAULT_OUTPUT_DIR = Path(os.environ.get("MELON_OUTPUT_DIR", ROOT / "output")).expanduser()
DB_PATH = DATA_DIR / "jobs.sqlite"

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "5173"))

MELON_SONG_URL = "https://www.melon.com/song/detail.htm?songId={song_id}"
YTDLP_TIMEOUT_SECONDS = int(os.environ.get("YTDLP_TIMEOUT_SECONDS", "55"))
# Stable baseline before speed-up:
# YOUTUBE_DOWNLOAD_WORKERS = int(os.environ.get("YOUTUBE_DOWNLOAD_WORKERS", "2"))
# YOUTUBE_DELAY_MIN_SECONDS = float(os.environ.get("YOUTUBE_DELAY_MIN_SECONDS", "8"))
# YOUTUBE_DELAY_MAX_SECONDS = float(os.environ.get("YOUTUBE_DELAY_MAX_SECONDS", "15"))
# MELON_MIN_GAP_SECONDS = float(os.environ.get("MELON_MIN_GAP_SECONDS", "3.5"))
YOUTUBE_DOWNLOAD_WORKERS = int(os.environ.get("YOUTUBE_DOWNLOAD_WORKERS", "2"))
YOUTUBE_DELAY_MIN_SECONDS = float(os.environ.get("YOUTUBE_DELAY_MIN_SECONDS", "4"))
YOUTUBE_DELAY_MAX_SECONDS = float(os.environ.get("YOUTUBE_DELAY_MAX_SECONDS", "8"))
YOUTUBE_RATE_LIMIT_PAUSE_SECONDS = float(os.environ.get("YOUTUBE_RATE_LIMIT_PAUSE_SECONDS", "3600"))
MELON_MIN_GAP_SECONDS = float(os.environ.get("MELON_MIN_GAP_SECONDS", "3.0"))
SHEET_FLUSH_INTERVAL_SECONDS = float(os.environ.get("SHEET_FLUSH_INTERVAL_SECONDS", "5"))
SHEET_BATCH_SIZE = int(os.environ.get("SHEET_BATCH_SIZE", "50"))
