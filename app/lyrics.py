from __future__ import annotations

import json
import re
import ssl
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

from .models import SongRecord
from .utils import clean_lyrics


USER_AGENT = "melon-music-downloader/0.1 (local lyrics fallback)"


def enrich_lyrics(song: SongRecord) -> SongRecord:
    if song.lyrics.strip():
        return song

    lyrics = find_bugs_lyrics(song.crawled_song_name, song.crawled_singer_name)
    if not lyrics:
        lyrics = find_genie_lyrics(song.crawled_song_name, song.crawled_singer_name)
    if not lyrics:
        lyrics = find_lrclib_lyrics(song.crawled_song_name, song.crawled_singer_name)
    if not lyrics:
        lyrics = find_lyrics_ovh(song.crawled_song_name, song.crawled_singer_name)

    song.lyrics = lyrics
    return song


def find_lrclib_lyrics(title: str, artist: str) -> str:
    candidates = []
    for query in unique_queries(title, artist):
        params = urllib.parse.urlencode({"q": query})
        data = fetch_json(f"https://lrclib.net/api/search?{params}")
        if isinstance(data, list):
            candidates.extend(data)

    best = best_lrclib_match(candidates, title, artist)
    if not best:
        return ""

    lyrics = best.get("plainLyrics") or strip_lrc_timestamps(best.get("syncedLyrics", ""))
    return clean_lyrics(lyrics)


def find_lyrics_ovh(title: str, artist: str) -> str:
    artist_path = urllib.parse.quote(clean_query_text(artist), safe="")
    title_path = urllib.parse.quote(clean_query_text(title), safe="")
    data = fetch_json(f"https://api.lyrics.ovh/v1/{artist_path}/{title_path}")
    if isinstance(data, dict):
        return clean_lyrics(data.get("lyrics", ""))
    return ""


def best_lrclib_match(candidates: list[dict], title: str, artist: str) -> dict | None:
    best = None
    best_score = 0.0
    target_title = normalize_match_text(title)
    target_artist = normalize_match_text(artist)

    for item in candidates:
        if item.get("instrumental"):
            continue
        lyrics = item.get("plainLyrics") or item.get("syncedLyrics")
        if not lyrics:
            continue

        item_title = normalize_match_text(item.get("trackName") or item.get("name") or "")
        item_artist = normalize_match_text(item.get("artistName") or "")
        title_score = SequenceMatcher(None, target_title, item_title).ratio()
        artist_score = SequenceMatcher(None, target_artist, item_artist).ratio() if target_artist else 0.5
        score = title_score * 0.72 + artist_score * 0.28

        if score > best_score:
            best_score = score
            best = item

    return best if best_score >= 0.62 else None


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=12, context=context) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def unique_queries(title: str, artist: str) -> list[str]:
    title = clean_query_text(title)
    artist = clean_query_text(artist)
    simple_title = simplify_title(title)
    values = [
        f"{title} {artist}",
        f"{simple_title} {artist}",
        title,
    ]
    result = []
    seen = set()
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def clean_query_text(value: str) -> str:
    return " ".join(str(value or "").replace("/", " ").split())


def simplify_title(value: str) -> str:
    replacements = ("Explicit Ver.", "Edited Ver.", "Original Karaoke", "Instrumental", "Remastered Ver.")
    for replacement in replacements:
        value = value.replace(replacement, "")
    return " ".join(value.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ").split())


def normalize_match_text(value: str) -> str:
    value = simplify_title(clean_query_text(value)).casefold()
    return "".join(char for char in value if char.isalnum() or char.isspace()).strip()


def strip_lrc_timestamps(value: str) -> str:
    lines = []
    for line in str(value or "").splitlines():
        while line.startswith("[") and "]" in line:
            line = line.split("]", 1)[1]
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def fetch_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, timeout=12, context=context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except Exception:
        return ""


def best_custom_match(candidates: list[dict], title: str, artist: str) -> dict | None:
    best = None
    best_score = 0.0
    target_title = normalize_match_text(title)
    target_artist = normalize_match_text(artist)

    aliases = {
        "bts": "방탄소년단",
        "akmu": "악뮤",
        "iu": "아이유",
        "aespa": "에스파",
        "blackpink": "블랙핑크",
        "twice": "트와이스",
        "seventeen": "세븐틴",
        "ive": "아이브",
        "newjeans": "뉴진스",
        "le sserafim": "르세라핌",
        "itzy": "있지",
        "taeyeon": "태연",
        "g-idle": "(여자)아이들",
        "gidle": "(여자)아이들",
        "stray kids": "스트레이 키즈",
    }

    for item in candidates:
        item_title = normalize_match_text(item.get("title") or "")
        item_artist = normalize_match_text(item.get("artist") or "")
        title_score = SequenceMatcher(None, target_title, item_title).ratio()
        
        if (target_artist and item_artist and 
            (target_artist in item_artist or 
             item_artist in target_artist or 
             (target_artist in aliases and aliases[target_artist] in item_artist) or
             (item_artist in aliases and aliases[item_artist] in target_artist))):
            artist_score = 1.0
        else:
            artist_score = SequenceMatcher(None, target_artist, item_artist).ratio() if target_artist else 0.5
            
        score = title_score * 0.72 + artist_score * 0.28

        if score > best_score:
            best_score = score
            best = item

    return best if best_score >= 0.62 else None


def find_bugs_lyrics(title: str, artist: str) -> str:
    query = f"{title} {artist}"
    url = f"https://music.bugs.co.kr/search/track?q={urllib.parse.quote(query)}"
    html = fetch_html(url)
    if not html:
        return ""

    trs = html.split('<tr')
    candidates = []
    for tr in trs:
        track_match = re.search(r'/track/(\d+)', tr)
        if not track_match:
            continue
        track_id = track_match.group(1)
        title_match = re.search(r'<p[^>]+class=[\"\']title[\"\'][^>]*>.*?title=[\"\'](.*?)[\"\']', tr, re.DOTALL)
        artist_match = re.search(r'<p[^>]+class=[\"\']artist[\"\'][^>]*>.*?title=[\"\'](.*?)[\"\']', tr, re.DOTALL)
        
        c_title = title_match.group(1) if title_match else ''
        c_artist = artist_match.group(1) if artist_match else ''
        if track_id and c_title:
            candidates.append({
                "id": track_id,
                "title": c_title,
                "artist": c_artist
            })
            
    best = best_custom_match(candidates, title, artist)
    if not best:
        return ""
        
    track_url = f"https://music.bugs.co.kr/track/{best['id']}"
    track_html = fetch_html(track_url)
    if not track_html:
        return ""
        
    xmp_match = re.search(r'<xmp[^>]*>(.*?)</xmp>', track_html, re.DOTALL)
    if xmp_match:
        return clean_lyrics(xmp_match.group(1))
    return ""


def find_genie_lyrics(title: str, artist: str) -> str:
    query = f"{title} {artist}"
    url = f"https://www.genie.co.kr/search/searchMain?query={urllib.parse.quote(query)}"
    html = fetch_html(url)
    if not html:
        return ""

    trs = html.split('<tr')
    candidates = []
    for tr in trs:
        songid_match = re.search(r'songid=[\"\']?(\d+)', tr)
        if not songid_match:
            continue
        song_id = songid_match.group(1)
        title_match = re.search(r'class=[\"\']title ellipsis[\"\'][^>]*title=[\"\'](.*?)[\"\']', tr, re.DOTALL)
        artist_match = re.search(r'class=[\"\']artist ellipsis[\"\'][^>]*>([^<]*)</a>', tr, re.DOTALL)
        
        c_title = title_match.group(1).strip() if title_match else ''
        c_artist = artist_match.group(1).strip() if artist_match else ''
        if song_id and c_title:
            candidates.append({
                "id": song_id,
                "title": c_title,
                "artist": c_artist
            })

    best = best_custom_match(candidates, title, artist)
    if not best:
        return ""

    track_url = f"https://www.genie.co.kr/detail/songInfo?xgnm={best['id']}"
    track_html = fetch_html(track_url)
    if not track_html:
        return ""

    idx = track_html.find('id="pLyrics"')
    if idx != -1:
        p_start = track_html.find('<p>', idx)
        if p_start != -1:
            p_end = track_html.find('</p>', p_start)
            if p_end != -1:
                return clean_lyrics(track_html[p_start+3:p_end])
    return ""

