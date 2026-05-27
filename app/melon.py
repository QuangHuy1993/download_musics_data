from __future__ import annotations

import re
import time
import threading
import random
import requests

from .config import MELON_MIN_GAP_SECONDS, get_random_proxy, mask_proxy
from .models import SongRecord
from .utils import clean_lyrics, clean_text, extract_first

# ---------------------------------------------------------------------------
# Rate limiter cho Melon (Akamai WAF)
# Mục tiêu: chạy bền trên 1 máy/IP, không bắn đều như bot.
# Khi bị 406: thêm 15s penalty cho tất cả thread phía sau
# ---------------------------------------------------------------------------
_MELON_MIN_GAP: float = MELON_MIN_GAP_SECONDS
_melon_lock = threading.Lock()
_melon_next: float = 0.0
_session_request_count = 0
_session = None
_request_counter = 0


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",

    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/123.0 Safari/537.36",

    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36",

]

def build_headers():
    return {
    "User-Agent": random.choice(USER_AGENTS),

        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),

        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",

        "Referer": "https://www.melon.com/",

        "Connection": "keep-alive",
    }



def _is_placeholder_name(name: str) -> bool:
    """
    Trả về True nếu tên bài/ca sĩ bị hỏng encoding.

    Thuật toán:
      - Tách ra phần "ký tự đặc biệt" (non-ASCII, non-dấu câu)
      - Nếu không có ký tự đặc biệt nào → tên thuần Latin → HỢP LỆ (BTS, IU...)
      - Nếu có ký tự đặc biệt mà TẤT CẢ đều là '?' → BỊ HỎNG (????, ???? pt.1...)
      - Nếu có ký tự đặc biệt không phải '?' → HỢP LỆ (에잇, 相思 pt.1...)

    Ví dụ bị hỏng : '????', '???? pt.1', '???? (Live)', '????,', '? ? ?'
    Ví dụ hợp lệ  : 'BTS', 'IU', 'Dynamite', 'Something pt.1', '에잇', '相思 pt.1'
    """
    if not name or not name.strip():
        return True
    # Loại bỏ ký tự Latin (a-z, A-Z), số, khoảng trắng và dấu câu phổ biến
    # Phần còn lại là "ký tự thực" của tên: chữ Hàn, Trung, Nhật, hoặc '?'
    exotic = re.sub(r"[a-zA-Z0-9\s,.()\[\]/\-_'\"!*&]+", '', name.strip())
    # Không còn ký tự nào → tên thuần Latin → hợp lệ
    if not exotic:
        return False
    # Còn ký tự nhưng tất cả là '?' → encoding bị mất → hỏng
    return all(c == '?' for c in exotic)

def create_session():
    session = requests.Session()

    session.headers.update(build_headers())

    return session

def get_session():
    global _session
    global _session_request_count

    if _session is None:
        _session = create_session()

    if _session_request_count >= 300:
        try:
            _session.close()
        except:
            pass

        _session = create_session()
        _session_request_count = 0

    _session_request_count += 1

    return _session

def warmup_navigation(session, proxy=None):
    pages = [
    "https://www.melon.com/",
    "https://www.melon.com/chart/index.htm",
    ]

    try:
        page = random.choice(pages)

        session.get(
            page,
            headers=build_headers(),
            proxies={"http": proxy, "https": proxy} if proxy else None,
            timeout=10
        )

        time.sleep(random.uniform(2, 5))

    except:
        pass


def extract_meta_value(html: str, label: str) -> str:
    pattern = (
        r"<dt>\s*"
        + re.escape(label)
        + r"\s*</dt>\s*<dd[^>]*>(.*?)</dd>"
    )
    return clean_text(extract_first(html, pattern))


def split_genre(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[1]


def extract_count_text(html: str, element_id: str) -> str:
    return clean_text(extract_first(
        html,
        rf'<[^>]+id=["\']{re.escape(element_id)}["\'][^>]*>(.*?)</[^>]+>'
    ))


def extract_producers(html: str) -> dict[str, str]:
    result = {"작사": [], "작곡": [], "편곡": []}
    section = extract_first(
        html,
        r'<div[^>]+class=["\']section_prdcr["\'][^>]*>(.*?)</div>\s*<!--\s*//작사 / 작곡\s*-->',
    )
    if not section:
        return {"lyricist": "", "composer": "", "arranger": ""}

    for item in re.findall(r"<li>(.*?)</li>", section, flags=re.IGNORECASE | re.DOTALL):
        name = clean_text(extract_first(
            item,
            r'<a[^>]+class=["\']artist_name["\'][^>]*>(.*?)</a>'
        ))
        role = clean_text(extract_first(
            item,
            r'<span[^>]+class=["\']type["\'][^>]*>(.*?)</span>'
        ))
        if name and role in result and name not in result[role]:
            result[role].append(name)

    return {
        "lyricist": ", ".join(result["작사"]),
        "composer": ", ".join(result["작곡"]),
        "arranger": ", ".join(result["편곡"]),
    }


def fetch_text(url: str) -> str:
    global _melon_next
    global _request_counter

    retries = 3

    for attempt in range(retries):

        with _melon_lock:

            _request_counter += 1

            # Burst cooldown
            if not get_random_proxy() and _request_counter % 50 == 0:
                cooldown = random.randint(15, 30)

                print(f"[Cooldown] Sleeping {cooldown}s")

                time.sleep(cooldown)

            # Global request gap
            wait = _melon_next - time.monotonic()

            if wait > 0:
                time.sleep(wait)

            min_gap = 1.0 if get_random_proxy() else _MELON_MIN_GAP
            random_gap = random.uniform(0.2, 0.8) if get_random_proxy() else random.uniform(0.5, 3.0)

            _melon_next = (
                time.monotonic()
                + min_gap
                + random_gap
            )

        try:

            session = get_session()

            proxy = get_random_proxy()
            # Human-like navigation
            if random.random() < 0.3:
                warmup_navigation(session, proxy)

            proxy = get_random_proxy()
            if proxy:
                print(f"[Melon Crawl Proxy] -> {mask_proxy(proxy)}")
            response = session.get(
                url,
                headers=build_headers(),
                proxies={"http": proxy, "https": proxy} if proxy else None,
                timeout=15
            )

            response.raise_for_status()

            response.encoding = response.apparent_encoding

            return response.text

        except requests.HTTPError as e:

            status_code = e.response.status_code

            if status_code == 406:

                with _melon_lock:
                    _melon_next = max(
                        _melon_next,
                        time.monotonic() + _MELON_MIN_GAP + 15.0
                    )

                if attempt < retries - 1:
                    continue

            if attempt < retries - 1:
                continue

            raise

        except (
            requests.ConnectionError,
            requests.Timeout,
            OSError
        ):

            with _melon_lock:
                _melon_next = max(
                    _melon_next,
                    time.monotonic() + _MELON_MIN_GAP + 3.0
                )

            if attempt < retries - 1:
                continue

            raise

    raise RuntimeError(
        f"Khong the lay du lieu Melon sau {retries} lan thu: {url}"
    )


def crawl_song(song: SongRecord) -> SongRecord:
    """
    Crawl tên bài, nghệ sĩ và lyrics từ Melon.
    """
    # Nếu đã có sẵn dữ liệu crawl từ trước (do chạy lại bài hát cũ hoặc retry), bỏ qua crawl Melon để tiết kiệm request
    if song.crawled_song_name and not _is_placeholder_name(song.crawled_song_name) and song.lyrics:
        print(f"[Melon Cache] Bo qua crawl cho bai: {song.crawled_song_name} (Da co san metadata)")
        return song

    # Bước 1: Cấu hình fallback từ Excel (sẽ bị ghi đè nếu Melon thành công)
    song.crawled_song_name = song.input_song_name or ""
    song.crawled_singer_name = song.input_singer_name or ""
    song.lyrics = song.input_lyrics or ""

    # Bước 2: Crawl Melon — đây là nguồn dữ liệu chính
    try:
        html = fetch_text(song.input_song_url)

        title = clean_text(extract_first(html, r'<div[^>]+class=["\']song_name["\'][^>]*>(.*?)</div>'))
        title = title.removeprefix("곡명").strip()
        artist = clean_text(extract_first(html, r'<a[^>]+class=["\']artist_name["\'][^>]*>.*?<span[^>]*>(.*?)</span>'))
        lyrics_html = extract_first(html, r'<div[^>]+id=["\']d_video_summary["\'][^>]*>(.*?)</div>')
        album = extract_meta_value(html, "앨범")
        release_date = extract_meta_value(html, "발매일")
        genre = extract_meta_value(html, "장르")
        major_genre, sub_genre = split_genre(genre)
        producers = extract_producers(html)
        like_count = extract_count_text(html, "d_like_count")
        comment_count = extract_count_text(html, "revCnt")

        # Ghi đè fallback chỉ khi Melon trả về giá trị thực
        if title and not _is_placeholder_name(title):
            song.crawled_song_name = title
        if artist and not _is_placeholder_name(artist):
            song.crawled_singer_name = artist
        if lyrics_html:
            song.lyrics = clean_lyrics(lyrics_html)
        if album:
            song.album = album
        if major_genre:
            song.major_genre = major_genre
        if sub_genre:
            song.sub_genre = sub_genre
        song.release_date = release_date or song.release_date
        song.lyricist = producers["lyricist"] or song.lyricist
        song.composer = producers["composer"] or song.composer
        song.arranger = producers["arranger"] or song.arranger
        song.like_count = like_count or song.like_count
        song.comment_count = comment_count or song.comment_count

    except Exception as e:
        # Bước 3: Melon thất bại → kiểm tra chất lượng dữ liệu fallback
        if _is_placeholder_name(song.crawled_song_name):
            # Excel cũng bị ???? → raise để worker mark_failed → retry sau
            # Điều này đảm bảo tên ???? KHÔNG BAO GIỜ được lưu vào DB
            raise RuntimeError(
                f"Melon that bai va ten Excel bi hong (???): {str(e)}"
            )
        # Excel có tên hợp lệ → dùng fallback, tiếp tục tải nhạc
        print(f"[Melon Warning] Row {song.source_row}: {str(e)} — dung fallback Excel")

    return song
