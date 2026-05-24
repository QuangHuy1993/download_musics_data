from __future__ import annotations

import os
import shutil
import subprocess
import time
import unicodedata
import re
import sys
import ssl
import urllib.request
import urllib.parse
import random
import threading
from pathlib import Path

from .config import (
    DATA_DIR,
    YTDLP_TIMEOUT_SECONDS,
    YOUTUBE_DELAY_MAX_SECONDS,
    YOUTUBE_DELAY_MIN_SECONDS,
    YOUTUBE_DOWNLOAD_WORKERS,
    YOUTUBE_RATE_LIMIT_PAUSE_SECONDS,
)

def get_ytdlp_path() -> str:
    configured = os.environ.get("YTDLP_PATH", "").strip()
    if configured and Path(configured).exists():
        return configured

    bin_dir = os.environ.get("MELON_BIN_DIR", "").strip()
    interpreter_dir = Path(sys.executable).parent
    binary_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"

    if bin_dir:
        bundled_ytdlp = Path(bin_dir) / binary_name
        if bundled_ytdlp.exists():
            return str(bundled_ytdlp)

    local_ytdlp = interpreter_dir / binary_name
    if local_ytdlp.exists():
        return str(local_ytdlp)
    found = shutil.which("yt-dlp")
    if found:
        return found
    return "yt-dlp"
from .models import SongRecord
from .utils import sanitize_path


class YouTubeRateLimitError(RuntimeError):
    pass


_YOUTUBE_SEMAPHORE = threading.BoundedSemaphore(max(1, YOUTUBE_DOWNLOAD_WORKERS))
_youtube_lock = threading.Lock()
_youtube_next = 0.0
_youtube_pause_until = 0.0
_youtube_rate_limited = False


def download_original_audio(song: SongRecord, output_dir: Path) -> SongRecord:
    folder_name = sanitize_path(f"{song.crawled_song_name} - {song.crawled_singer_name} [{song.song_id}]")
    file_name = sanitize_path(f"{song.crawled_song_name} - {song.crawled_singer_name} [{song.song_id}]")
    song_dir = output_dir / folder_name
    temp_dir = output_dir / ".tmp" / f"{song.song_id}-{int(time.time() * 1000)}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        song.audio_path = try_download_queries(song, temp_dir, file_name)
        if not song.audio_path:
            raise RuntimeError("yt-dlp ket thuc nhung khong tim thay file audio da tai.")

        if song_dir.exists():
            shutil.rmtree(song_dir)
        song_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(song.audio_path)
        shutil.move(str(temp_dir), str(song_dir))
        song.audio_path = str(song_dir / temp_path.name)
        return song
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    finally:
        remove_empty_dir(temp_dir.parent)


def find_youtube_videos(song_name: str, artist_name: str, max_results: int = 5) -> list[str]:
    """
    Trả về danh sách tối đa max_results video IDs phù hợp.
    Danh sách ưu tiên: strict match trước, fallback sau.
    Trả về nhiều kết quả để try_download_queries có thể thử từng cái khi
    gặp video bị age-restrict hoặc không tải được.
    """
    clean_artist = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', artist_name)
    clean_artist = " ".join(clean_artist.split()).strip()

    title_clean = song_name.replace("19금", "").strip()

    query = f"{title_clean} {clean_artist}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    context = ssl._create_unverified_context()

    norm_song = re.sub(r'\s+', '', title_clean.lower())
    norm_artist = re.sub(r'\s+', '', clean_artist.lower())
    is_inst = any(kw in title_clean.lower() for kw in ("instrumental", "inst", "반주", "mr"))

    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    blocks = html.split('"videoRenderer":')
    if len(blocks) <= 1:
        return []

    strict_ids: list[str] = []
    fallback_ids: list[str] = []

    for block in blocks[1:15]:   # scan top 15 để có đủ candidates
        id_match = re.search(r'"videoId"\s*:\s*"([^"]+)"', block)
        if not id_match:
            continue
        video_id = id_match.group(1)

        title_match = re.search(r'"title"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"', block)
        video_title = title_match.group(1) if title_match else ""
        try:
            video_title = video_title.encode('utf-8').decode('unicode_escape', errors='ignore')
        except Exception:
            pass

        channel_match = re.search(r'"ownerText"\s*:\s*\{\s*"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"', block)
        channel_name = ""
        if channel_match:
            channel_name = channel_match.group(1)
            try:
                channel_name = channel_name.encode('utf-8').decode('unicode_escape', errors='ignore')
            except Exception:
                pass

        length_match = re.search(r'"lengthText"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', block)
        length_str = length_match.group(1) if length_match else ""
        duration_sec = 0
        if length_str:
            parts = length_str.split(":")
            try:
                if len(parts) == 2:
                    duration_sec = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                pass

        norm_v_title = re.sub(r'\s+', '', video_title.lower())
        norm_channel = re.sub(r'\s+', '', channel_name.lower())

        # Hard filters (bất kể strict hay fallback)
        if duration_sec > 600 or (0 < duration_sec < 45):
            continue
        if "cover" in norm_v_title and "cover" not in norm_song:
            continue
        if "karaoke" in norm_v_title and "karaoke" not in norm_song:
            continue
        if "tj" in norm_channel or "금영" in norm_v_title:
            continue
        if not is_inst and ("inst" in norm_v_title or "instrumental" in norm_v_title or "반주" in norm_v_title) and "inst" not in norm_song:
            continue

        # Strict match: cả tên bài lẫn nghệ sĩ đều có
        has_song = norm_song in norm_v_title or any(
            part in norm_v_title
            for part in re.split(r'[^a-zA-Z0-9가-힣]', title_clean.lower()) if len(part) > 2
        )
        has_artist = (
            norm_artist in norm_v_title
            or norm_artist in norm_channel
            or any(
                part in norm_v_title or part in norm_channel
                for part in re.split(r'[^a-zA-Z0-9가-힣]', clean_artist.lower()) if len(part) > 2
            )
        )

        if has_song and (has_artist or "topic" in norm_channel):
            if video_id not in strict_ids:
                strict_ids.append(video_id)
        else:
            if video_id not in fallback_ids:
                fallback_ids.append(video_id)

        if len(strict_ids) >= max_results:
            break

    # Strict match ưu tiên, sau đó mới fallback
    candidates = strict_ids + fallback_ids
    return candidates[:max_results]


def find_youtube_video(song_name: str, artist_name: str) -> str | None:
    """Legacy wrapper — trả về video ID đầu tiên hoặc None."""
    results = find_youtube_videos(song_name, artist_name, max_results=1)
    return results[0] if results else None


# Từ khoá trong lỗi yt-dlp cho biết video bị chặn/hạn chế cụ thể.
# Phải đủ chính xác để không nhầm với lỗi mạng/timeout.
_SKIP_ERROR_KEYWORDS = (
    "sign in to confirm",         # age-restricted
    "age-restricted",              # age-restricted (dạng khác)
    "private video",               # video riêng tư
    "video unavailable",           # video không tồn tại
    "this video has been removed", # đã bị xóa
    "requested format is not available",  # format không tồn tại trên video này
    "no video formats found",      # không có format nào
    "members-only",                # video chỉ dành cho thành viên
    "copyright",                   # bị chặn do bản quyền
)

_RATE_LIMIT_KEYWORDS = (
    "rate-limited by youtube",
    "current session has been rate-limited",
    "try again later",
    "too many requests",
    "http error 429",
)


def _is_skippable_error(error: str) -> bool:
    """Trả về True nếu lỗi là do video bị chặn/hạn chế (nên thử video khác)."""
    if _is_youtube_rate_limited(error):
        return False
    lower = error.lower()
    return any(kw in lower for kw in _SKIP_ERROR_KEYWORDS)


def try_download_queries(song: SongRecord, temp_dir: Path, file_name: str) -> str:
    with _YOUTUBE_SEMAPHORE:
        if is_youtube_rate_limited():
            raise YouTubeRateLimitError(rate_limit_message("YouTube dang bi rate-limit."))
        _wait_youtube_turn()
        # Lấy tối đa 5 video candidates thay vì 1
        video_ids = find_youtube_videos(song.crawled_song_name, song.crawled_singer_name, max_results=5)
        if not video_ids:
            raise RuntimeError("Khong tim thay video phu hop tren YouTube.")

        last_error = ""
        for video_id in video_ids:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            clear_audio_files(temp_dir)
            _wait_youtube_turn()
            audio_path, error = run_ytdlp(video_url, temp_dir, file_name)
            if audio_path:
                return audio_path
            last_error = error
            if _is_youtube_rate_limited(error):
                _pause_youtube_downloads()
                raise YouTubeRateLimitError(rate_limit_message(error))
            # Nếu lỗi do video bị age-restrict / private / bị xóa → thử video tiếp
            if _is_skippable_error(error):
                continue
            # Lỗi khác (timeout, mạng…) → dừng ngay, không thử tiếp
            break

    raise RuntimeError(last_error or "Khong tai duoc audio tu bat ky video nao.")


def _wait_youtube_turn():
    global _youtube_next
    while True:
        with _youtube_lock:
            if _youtube_rate_limited:
                raise YouTubeRateLimitError(rate_limit_message("YouTube dang bi rate-limit."))
            now = time.monotonic()
            wait = max(_youtube_pause_until, _youtube_next) - now
            if wait <= 0:
                delay = random.uniform(YOUTUBE_DELAY_MIN_SECONDS, YOUTUBE_DELAY_MAX_SECONDS)
                _youtube_next = now + delay
                return
        time.sleep(min(wait, 5.0))


def _pause_youtube_downloads():
    global _youtube_pause_until, _youtube_rate_limited
    with _youtube_lock:
        _youtube_rate_limited = True
        _youtube_pause_until = max(
            _youtube_pause_until,
            time.monotonic() + YOUTUBE_RATE_LIMIT_PAUSE_SECONDS,
        )


def is_youtube_rate_limited() -> bool:
    with _youtube_lock:
        return _youtube_rate_limited


def rate_limit_message(detail: str) -> str:
    return (
        "YOUTUBE_RATE_LIMIT: YouTube da gioi han session hien tai. "
        "Tool da tam dung queue YouTube. Hay doi cookie YouTube hoac cho khoang "
        f"{int(YOUTUBE_RATE_LIMIT_PAUSE_SECONDS / 60)} phut roi bam Start/Resume lai. "
        f"Chi tiet: {detail}"
    )


def _is_youtube_rate_limited(error: str) -> bool:
    lower = str(error or "").lower()
    return any(keyword in lower for keyword in _RATE_LIMIT_KEYWORDS)


def normalize_query_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return " ".join(value.split())


def simplify_title(value: str) -> str:
    simplified = value
    for phrase in ("Explicit Ver.", "Edited Ver.", "Original Karaoke", "Instrumental Rock Version"):
        simplified = simplified.replace(phrase, "")
    simplified = re.sub(r"[\[\]()]+", " ", simplified)
    simplified = re.sub(r"\s+", " ", simplified)
    return simplified.strip(" -:")


def unique_values(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def run_ytdlp(query: str, temp_dir: Path, file_name: str, cookies_browser: str = None) -> tuple[str, str]:
    output_template = str(temp_dir / f"{file_name}.%(ext)s")
    # Format priority:
    # 1. m4a audio-only (fastest, no ffmpeg needed)
    # 2. Any best audio-only stream (webm/opus etc, ffmpeg converts to m4a)
    # 3. Worst case: audio-only from video container — still avoid full video
    # We do NOT fall back to video formats (best[ext=mp4]/best) because that
    # downloads several hundred MB of video unnecessarily and hangs.
    cmd = [
        get_ytdlp_path(),
        query,
        "-f",
        "bestaudio[ext=m4a]/bestaudio[acodec!=none]/bestaudio",
        "-x",
        "--audio-format",
        "m4a",
        "--audio-quality",
        "0",
        "--no-check-certificates",
        "--js-runtimes",
        "node",
        "--concurrent-fragments",
        "1",
        "--sleep-requests",
        "1.5",
        "--sleep-interval",
        str(YOUTUBE_DELAY_MIN_SECONDS),
        "--max-sleep-interval",
        str(YOUTUBE_DELAY_MAX_SECONDS),
        "--socket-timeout",
        "15",
        "--retries",
        "1",
        "--fragment-retries",
        "1",
        "--extractor-retries",
        "1",
        "--match-filter",
        "!is_live",
        "--no-part",
        "--ignore-errors",
        "--no-abort-on-error",
        "--max-downloads",
        "1",
        "--no-simulate",
        "--print",
        "after_move:filepath",
        "-o",
        output_template,
    ]
    if cookies_browser:
        cmd.extend(["--cookies-from-browser", cookies_browser])
        # Also cache them to data/cookies.txt for future speedup!
        cookie_file = DATA_DIR / "cookies.txt"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--cookies", str(cookie_file)])
    else:
        cookie_file = DATA_DIR / "cookies.txt"
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            cmd.extend(["--cookies", str(cookie_file)])

    stdout_buf: list[str] = []
    stderr_buf: list[str] = []
    proc = None
    try:
        # Use Popen instead of run() so that we can kill child processes
        # (including ffmpeg spawned by yt-dlp) on timeout, preventing zombie hangs.
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            stdout_data, stderr_data = proc.communicate(timeout=YTDLP_TIMEOUT_SECONDS)
            stdout_buf.append(stdout_data or "")
            stderr_buf.append(stderr_data or "")
        except subprocess.TimeoutExpired:
            # Kill the entire process group so ffmpeg subprocesses are also terminated
            proc.kill()
            proc.communicate()  # Drain pipes to prevent deadlock
            raise RuntimeError(f"yt-dlp timeout {YTDLP_TIMEOUT_SECONDS}s")
    finally:
        if proc and proc.poll() is None:
            proc.kill()

    discovered = find_audio_file(temp_dir)
    if discovered:
        return str(discovered), ""

    stdout_text = "".join(stdout_buf)
    stderr_text = "".join(stderr_buf)
    output = clean_process_output(stderr_text + "\n" + stdout_text)
    if proc.returncode != 0:
        return "", output or f"yt-dlp loi voi query: {query}"
    return "", f"Khong co file audio sau khi chay query: {query}\n{output}".strip()


def extract_browser_cookies(browser_name: str) -> tuple[bool, str]:
    cookie_file = DATA_DIR / "cookies.txt"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # We run a dummy query with --skip-download to dump cookies
    cmd = [
        get_ytdlp_path(),
        "--cookies-from-browser",
        browser_name,
        "--cookies",
        str(cookie_file),
        "--skip-download",
        "--no-check-certificates",
        "--remote-components",
        "ejs:github",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if cookie_file.exists() and cookie_file.stat().st_size > 0:
            return True, f"Trích xuất thành công {cookie_file.stat().st_size} bytes cookie từ {browser_name}."
        
        output = (result.stderr or "") + "\n" + (result.stdout or "")
        return False, f"Không thể trích xuất cookie từ {browser_name}. Chi tiết:\n{output}"
    except Exception as e:
        return False, f"Lỗi chạy lệnh trích xuất: {str(e)}"


def clean_process_output(value: str) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return "\n".join(lines[-8:])


def to_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def find_audio_file(song_dir: Path) -> Path | None:
    candidates = []
    for suffix in (".m4a", ".webm", ".opus", ".mp3", ".aac"):
        candidates.extend(song_dir.glob(f"*{suffix}"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def clear_audio_files(song_dir: Path):
    for path in song_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".aac"}:
            path.unlink(missing_ok=True)


def remove_empty_dir(path: Path):
    try:
        path.rmdir()
    except OSError:
        pass
