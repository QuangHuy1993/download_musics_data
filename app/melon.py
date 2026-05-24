from __future__ import annotations

import ssl
import urllib.request
import urllib.error
import re
import time
import threading
import http.cookiejar
import random

from .config import MELON_MIN_GAP_SECONDS
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

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.melon.com/",
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


def fetch_text(url: str) -> str:
    """
    Gửi request tới Melon với rate limit chính xác.
    Gap được tính từ ĐẦU request này đến ĐẦU request tiếp theo
    (không phải từ cuối → đầu), nên HTTP latency không bị cộng thêm.
    Có jitter nhẹ để tránh pattern đều tuyệt đối.
    """
    global _melon_next

    retries = 3
    for attempt in range(retries):
        # Chờ trong lock đến khi đến lượt, rồi đặt lịch cho request kế tiếp
        with _melon_lock:
            wait = _melon_next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _melon_next = time.monotonic() + _MELON_MIN_GAP + random.uniform(0.2, 1.2)

        # Gửi request NGOÀI lock → các thread khác có thể pre-book slot chờ
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with _opener.open(req, timeout=15) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")

        except urllib.error.HTTPError as e:
            if e.code == 406:
                # WAF block → cộng thêm 15s penalty để tất cả thread sau tự chờ
                with _melon_lock:
                    _melon_next = max(_melon_next, time.monotonic() + _MELON_MIN_GAP + 15.0)
                if attempt < retries - 1:
                    continue
                raise
            # Lỗi HTTP khác (404, 5xx...)
            if attempt < retries - 1:
                continue
            raise

        except (urllib.error.URLError, TimeoutError, OSError):
            # Mất mạng tạm thời → thêm 3s buffer
            with _melon_lock:
                _melon_next = max(_melon_next, time.monotonic() + _MELON_MIN_GAP + 3.0)
            if attempt < retries - 1:
                continue
            raise

    raise RuntimeError(f"Khong the lay du lieu Melon sau {retries} lan thu: {url}")


def crawl_song(song: SongRecord) -> SongRecord:
    """
    Crawl tên bài, nghệ sĩ và lyrics từ Melon.

    Logic ưu tiên (quan trọng — đọc kỹ):
      1. Melon thành công → dùng data Melon (nguồn chính xác nhất)
      2. Melon thất bại + Excel có tên hợp lệ → dùng Excel (vẫn tải được nhạc)
      3. Melon thất bại + Excel bị ???? → raise RuntimeError
         → worker.py sẽ mark_failed → song được retry ở lần chạy sau
         → KHÔNG bao giờ lưu tên ???? vào database
    """
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

        # Ghi đè fallback chỉ khi Melon trả về giá trị thực
        if title and not _is_placeholder_name(title):
            song.crawled_song_name = title
        if artist and not _is_placeholder_name(artist):
            song.crawled_singer_name = artist
        if lyrics_html:
            song.lyrics = clean_lyrics(lyrics_html)

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
