from __future__ import annotations

import os
import shutil
import subprocess
import time
import unicodedata
import re
import sys
import random
import threading
import app.db as db

from pathlib import Path
from .utils import sanitize_path



class YouTubeRateLimitError(Exception):
    pass 


class _YtdlpQuietLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


# Prefer yt_dlp but be tolerant if import style differs.
try:
    from yt_dlp import YoutubeDL
except Exception:
    try:
        import yt_dlp as _yt_dlp
        YoutubeDL = _yt_dlp.YoutubeDL
    except Exception as exc:
        raise ImportError(
            "yt_dlp is required by app.downloader. Install with: pip install yt-dlp"
        ) from exc

from .config import (
    DATA_DIR,
    YTDLP_TIMEOUT_SECONDS,
    YOUTUBE_DELAY_MAX_SECONDS,
    YOUTUBE_DELAY_MIN_SECONDS,
    YOUTUBE_DOWNLOAD_WORKERS,
    YOUTUBE_RATE_LIMIT_PAUSE_SECONDS,
)

# Minimal, robust downloader utilities

def _is_youtube_auth_error(error: Exception | str) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "sign in",
            "login",
            "cookie",
            "cookies",
            "confirm you're not a bot",
            "not a bot",
            "verify",
            "verification",
            "403",
            "429",
        )
    )

def _normalize_search_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"\bofficial\s+audio\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\btopic\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[\[\](){}'\"`~!@#$%^&*_+=|\\/:;,.?<>-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _unique_search_queries(title: str, artist: str) -> list[str]:
    title = re.sub(r"\s+", " ", title or "").strip()
    artist = re.sub(r"\s+", " ", artist or "").strip()
    candidates = [
        f"{title} {artist} official audio",
        f"{title} {artist}",
        f"{title} {artist} Topic",
        title,
    ]

    seen: set[str] = set()
    queries: list[str] = []
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip()
        key = candidate.lower()
        if candidate and key not in seen:
            queries.append(candidate)
            seen.add(key)
    return queries


def _score_youtube_candidate(video: dict, title: str, artist: str) -> int:
    expected_title = _normalize_search_text(title)
    expected_artist = _normalize_search_text(artist)
    video_title = _normalize_search_text(str(video.get("title") or ""))
    uploader = _normalize_search_text(
        str(video.get("uploader") or video.get("channel") or video.get("channel_id") or "")
    )
    haystack = f"{video_title} {uploader}"

    score = 0
    if expected_title and expected_title in video_title:
        score += 60
    else:
        title_tokens = [token for token in expected_title.split() if len(token) >= 2]
        if title_tokens:
            matched = sum(1 for token in title_tokens if token in video_title)
            score += int(45 * matched / len(title_tokens))

    if expected_artist and expected_artist in haystack:
        score += 25
    else:
        artist_tokens = [token for token in expected_artist.split() if len(token) >= 2]
        if artist_tokens:
            matched = sum(1 for token in artist_tokens if token in haystack)
            score += int(18 * matched / len(artist_tokens))

    if "official audio" in str(video.get("title") or "").lower():
        score += 8
    if "topic" in str(video.get("channel") or video.get("uploader") or "").lower():
        score += 8

    duration = video.get("duration")
    if isinstance(duration, (int, float)) and 45 <= duration <= 900:
        score += 5

    source_text = f"{video.get('title') or ''} {video.get('description') or ''}"
    text = source_text.lower()
    normalized_text = f" {_normalize_search_text(source_text)} "
    normalized_expected = f" {_normalize_search_text(title)} "
    if any(bad in text for bad in ("cover", "karaoke", "reaction", "live cam")):
        score -= 20
    if not any(marker in normalized_expected for marker in (" inst ", " instrumental ", " mr ", " 반주 ")):
        if any(marker in normalized_text for marker in (" inst ", " instrumental ", " mr ", " 반주 ")):
            score -= 40

    return score


def _youtube_cache_key(title: str, artist: str) -> str:
    return f"audio-only-v2::{title}::{artist}".strip(":")


def search_youtube_candidates(title: str, artist: str = "", max_results: int = 12, use_cookies: bool = True) -> list[dict]:
    candidates_by_id: dict[str, tuple[int, dict]] = {}

    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "logger": _YtdlpQuietLogger(),
        "skip_download": True,
        "default_search": "ytsearch",
        "noplaylist": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "extractor_args": {"youtube": {"player_client": ["ios", "android"]}},
    }
    cookie_file = DATA_DIR / "cookies.txt"
    if use_cookies and cookie_file.exists():
        ydl_opts["cookiefile"] = str(cookie_file)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            for query in _unique_search_queries(title, artist):
                search_query = f"ytsearch8:{query}"
                print(f"[YT SEARCH] {search_query}")
                info = ydl.extract_info(search_query, download=False)
                entries = (info or {}).get("entries") or []
                for video in entries:
                    if not video or not video.get("id"):
                        continue
                    score = _score_youtube_candidate(video, title, artist)
                    video_id = video["id"]
                    current = candidates_by_id.get(video_id)
                    if current is None or score > current[0]:
                        candidates_by_id[video_id] = (score, video)
    except Exception as e:
        if use_cookies and cookie_file.exists():
            print(f"YouTube search failed with cookies, retrying without cookies: {e}")
            return search_youtube_candidates(title, artist, max_results, use_cookies=False)
        print(f"YouTube search failed: {e}")
        if _is_youtube_auth_error(e):
            raise YouTubeRateLimitError(
                f"YouTube yeu cau cookie/xac minh moi khi search: {e}"
            ) from e
        return []

    ranked = sorted(candidates_by_id.values(), key=lambda item: item[0], reverse=True)
    return [
        {
            "id": video.get("id"),
            "title": video.get("title"),
            "url": f"https://www.youtube.com/watch?v={video.get('id')}",
            "score": score,
        }
        for score, video in ranked[:max_results]
        if video.get("id")
    ]


def _score_soundcloud_candidate(track: dict, title: str, artist: str) -> int:
    expected_title = _normalize_search_text(title)
    expected_artist = _normalize_search_text(artist)
    track_title = _normalize_search_text(str(track.get("title") or ""))
    uploader = _normalize_search_text(
        str(track.get("uploader") or track.get("channel") or "")
    )
    haystack = f"{track_title} {uploader}"

    score = 0
    if expected_title and expected_title in track_title:
        score += 60
    else:
        title_tokens = [token for token in expected_title.split() if len(token) >= 2]
        if title_tokens:
            matched = sum(1 for token in title_tokens if token in track_title)
            score += int(45 * matched / len(title_tokens))

    if expected_artist and expected_artist in haystack:
        score += 25
    else:
        artist_tokens = [token for token in expected_artist.split() if len(token) >= 2]
        if artist_tokens:
            matched = sum(1 for token in artist_tokens if token in haystack)
            score += int(18 * matched / len(artist_tokens))

    duration = track.get("duration")
    if isinstance(duration, (int, float)):
        if 45 <= duration <= 900:
            score += 5
        elif duration > 900:
            score -= 40

    title_lower = str(track.get("title") or "").lower()
    if any(bad in title_lower for bad in ("cover", "karaoke", "reaction", "remix", "mix")):
        score -= 20

    return score


def search_soundcloud_candidates(title: str, artist: str = "", max_results: int = 12) -> list[dict]:
    candidates_by_id: dict[str, tuple[int, dict]] = {}

    ydl_opts = {
        "quiet": True,
        "noprogress": True,
        "logger": _YtdlpQuietLogger(),
        "skip_download": True,
        "default_search": "scsearch",
        "noplaylist": True,
        "extract_flat": True,
        "ignoreerrors": True,
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            for query in _unique_search_queries(title, artist):
                search_query = f"scsearch8:{query}"
                print(f"[SOUNDCLOUD SEARCH] {search_query}")
                info = ydl.extract_info(search_query, download=False)
                entries = (info or {}).get("entries") or []
                for track in entries:
                    if not track or not track.get("url"):
                        continue
                    score = _score_soundcloud_candidate(track, title, artist)
                    track_url = track["url"]
                    current = candidates_by_id.get(track_url)
                    if current is None or score > current[0]:
                        candidates_by_id[track_url] = (score, track)
    except Exception as e:
        print(f"SoundCloud search failed: {e}")
        return []

    ranked = sorted(candidates_by_id.values(), key=lambda item: item[0], reverse=True)
    return [
        {
            "id": track.get("id") or track.get("url"),
            "title": track.get("title"),
            "url": track.get("url"),
            "score": score,
        }
        for score, track in ranked[:max_results]
        if track.get("url")
    ]


def search_youtube_video(title: str, artist: str = ""):
    cache_key = _youtube_cache_key(title, artist)

    # Check cache first
    cached_video_id = db.get_cached_video(cache_key)

    if cached_video_id:
        return {
            "id": cached_video_id,
            "title": title,
            "url": f"https://www.youtube.com/watch?v={cached_video_id}"
        }

    candidates = search_youtube_candidates(title, artist, max_results=1)
    return candidates[0] if candidates else None


def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)

def _sanitize_filename(name: str) -> str:
    # Basic sanitization to produce filesystem-safe names
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[<>:\"/\\|?*\n\r\t]+", "_", name)
    name = name.strip()
    return name or "download"

def download_url(url: str, output_stem: Path, timeout: int | None = None, use_cookies: bool = True) -> tuple[str, dict]:
    """
    Download a single URL as m4a audio-only.
    """
    timeout = timeout or YTDLP_TIMEOUT_SECONDS
    output_stem.parent.mkdir(parents=True, exist_ok=True)

    outtmpl = str(output_stem.with_suffix(".%(ext)s"))

    base_opts = {
        "outtmpl": outtmpl,

        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _YtdlpQuietLogger(),
        "no_warnings": True,
        "skip_download": False,
        "format": "bestaudio[ext=m4a]/bestaudio[acodec!=none]/bestaudio",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "0",
            }
        ],

        "socket_timeout": timeout,

        "sleep_interval_requests": 0.5,
        "max_sleep_interval": 2,

        "concurrent_fragment_downloads": 1,

        "retries": 1,

        "fragment_retries": 1,
        "extractor_retries": 1,

        "http_chunk_size": 10485760,
        "http_headers": {
        "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )
        },

    }
    cookie_opts = {}
    cookie_file = DATA_DIR / "cookies.txt"
    if use_cookies and cookie_file.exists():
        cookie_opts["cookiefile"] = str(cookie_file)

    last_error = None

    ydl_opts = {**base_opts, **cookie_opts}
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = info.get("requested_downloads") or []
            if requested and requested[0].get("filepath"):
                filepath = Path(requested[0]["filepath"])
                m4a_path = filepath.with_suffix(".m4a")
                if m4a_path.exists():
                    return str(m4a_path), info
                if filepath.exists() and filepath.suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".aac"}:
                    return str(filepath), info
            matches = sorted(
                [
                    path
                    for path in output_stem.parent.glob(f"{output_stem.name}.*")
                    if path.suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".aac"}
                ],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if matches:
                return str(matches[0]), info
            raise RuntimeError("yt-dlp finished but no audio output file was found")
    except Exception as exc:
        if use_cookies and cookie_file.exists():
            print(f"Failed to download with cookies, retrying without cookies: {exc}")
            return download_url(url, output_stem, timeout, use_cookies=False)

        last_error = exc
        if _is_youtube_auth_error(exc):
            raise YouTubeRateLimitError(
                f"YouTube yeu cau cookie/xac minh moi khi download: {exc}"
            ) from exc

    raise RuntimeError(f"Failed to download {url}: {last_error}") from last_error

# Simple rate-limited downloader loop for a list of URLs
def download_many(urls: list[str], dest_dir: str | None = None) -> list[str]:
    results: list[str] = []
    dest_dir = dest_dir or DATA_DIR
    for i, url in enumerate(urls):

        if i > 0 and i % 20 == 0:

            cooldown = random.randint(60, 180)

            print(f"[YouTube Cooldown] {cooldown}s")

            time.sleep(cooldown)

        delay = random.uniform(
            YOUTUBE_DELAY_MIN_SECONDS,
            YOUTUBE_DELAY_MAX_SECONDS
        )

        time.sleep(delay)

        output_stem = Path(dest_dir) / _sanitize_filename(f"download_{i + 1}")
        path, _info = download_url(url, output_stem)

        results.append(path)

        # Periodic pause to respect rate limit policies
        if (i + 1) % max(1, YOUTUBE_DOWNLOAD_WORKERS) == 0:
            time.sleep(YOUTUBE_RATE_LIMIT_PAUSE_SECONDS)
    return results

def download_original_audio(song, output_dir):
    """
    Download audio for a song object.
    """
    title = song.crawled_song_name or song.input_song_name
    artist = song.crawled_singer_name or song.input_singer_name
    query = f"{title} {artist} official audio".strip()
    cache_key = _youtube_cache_key(title, artist)
    cached_video_id = db.get_cached_video(cache_key)
    candidates = []
    is_soundcloud = False

    if cached_video_id:
        if cached_video_id.startswith("http://") or cached_video_id.startswith("https://") or "soundcloud" in cached_video_id:
            candidates.append(
                {
                    "id": cached_video_id,
                    "title": title,
                    "url": cached_video_id,
                }
            )
            is_soundcloud = True
        else:
            candidates.append(
                {
                    "id": cached_video_id,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={cached_video_id}",
                }
            )

    if not candidates:
        try:
            candidates.extend(
                candidate
                for candidate in search_youtube_candidates(title, artist, max_results=4)
                if candidate["id"] != cached_video_id
            )
        except Exception as e:
            print(f"[YouTube Search Error] {e}. Falling back to SoundCloud search.")
            
        if not candidates:
            print(f"[SoundCloud Fallback] Searching SoundCloud for: {query}")
            try:
                candidates.extend(search_soundcloud_candidates(title, artist, max_results=4))
                is_soundcloud = True
            except Exception as se:
                print(f"[SoundCloud Search Error] {se}")

    if not candidates:
        raise RuntimeError(f"Khong tim thay video tren ca YouTube va SoundCloud: {query}")

    output_dir = Path(output_dir).resolve()
    folder_name = sanitize_path(
        f"{song.crawled_singer_name or 'unknown'} - {song.crawled_song_name or song.song_id or 'unknown'}"
    )

    track_dir = output_dir / "kpop" / "audio" / "folder_01" / folder_name
    audio_stem = track_dir / sanitize_path(song.crawled_song_name or song.song_id or "audio")

    last_error = ""
    audio_path = ""
    audio_info = {}
    selected_video = None
    try:
        for video in candidates:
            try:
                audio_path, audio_info = download_url(video["url"], audio_stem)
                selected_video = video
                break
            except YouTubeRateLimitError:
                # If YouTube rate limits download, try SoundCloud immediately as fallback
                if not is_soundcloud:
                    print("[YouTube Download Rate Limit] Trying SoundCloud fallback...")
                    sc_candidates = search_soundcloud_candidates(title, artist, max_results=4)
                    if sc_candidates:
                        for sc_video in sc_candidates:
                            try:
                                audio_path, audio_info = download_url(sc_video["url"], audio_stem)
                                selected_video = sc_video
                                is_soundcloud = True
                                break
                            except Exception as sc_exc:
                                last_error = str(sc_exc)
                                continue
                        if audio_path:
                            break
                raise
            except Exception as exc:
                last_error = str(exc)
                continue

        if not audio_path or not selected_video:
            raise RuntimeError(
                f"Khong tai duoc audio: {query}. Loi cuoi: {last_error}"
            )
    except Exception:
        if track_dir.exists():
            try:
                shutil.rmtree(track_dir)
            except Exception:
                pass
        raise

    db.save_cached_video(cache_key, selected_video["url"] if is_soundcloud else selected_video["id"])

    # lưu path thật trên máy
    song.audio_path = audio_path

    # path chuẩn khách yêu cầu cho jsonl
    audio_real_path = Path(audio_path)
    song.local_audio_path = audio_real_path.relative_to(output_dir).as_posix()
    lyric_real_path = audio_real_path.with_suffix(".krc")

    # tạo folder nếu chưa có
    lyric_real_path.parent.mkdir(parents=True, exist_ok=True)

    # save lyric file
    with open(lyric_real_path, "w", encoding="utf-8") as f:
        f.write(song.lyrics or "")

    # path cho jsonl
    song.lyric_path = lyric_real_path.relative_to(output_dir).as_posix()
    song.duration = round(float(audio_info.get("duration") or 0), 2)
    sample_rate = audio_info.get("asr") or 44100
    try:
        song.sample_rate = f"{float(sample_rate) / 1000:.1f}kHz"
    except (TypeError, ValueError):
        song.sample_rate = "44.1kHz"

    return song
