from __future__ import annotations

import re
from html import unescape


def extract_first(text: str, pattern: str) -> str:
    match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def clean_text(value: str) -> str:
    value = re.sub(r"<script[\s\S]*?</script>", " ", value or "", flags=re.IGNORECASE)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def clean_lyrics(value: str) -> str:

    if not value:
        return ""

    value = re.sub(
        r"<br\s*/?>",
        "\n",
        value,
        flags=re.IGNORECASE
    )

    value = re.sub(
        r"<script[\s\S]*?</script>",
        " ",
        value,
        flags=re.IGNORECASE
    )

    value = re.sub(
        r"<style[\s\S]*?</style>",
        " ",
        value,
        flags=re.IGNORECASE
    )

    value = re.sub(r"<[^>]+>", " ", value)

    value = unescape(value).replace("\u00a0", " ")

    # remove dangerous unicode chars for jsonl
    value = (
        value
        .replace("\u2028", " ")
        .replace("\u2029", " ")
        .replace("\x00", "")
    )

    lines = [
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in value.split("\n")
    ]

    value = "\n".join(
        line for line in lines
        if line
    ).strip()

    # remove fake melon lyrics
    bad_keywords = [
        "가사등록하기",
        "가사오류신고",
        "멜론 회원 여러분",
        "등록된 가사가 없습니다",
    ]

    for keyword in bad_keywords:
        if keyword in value:
            return ""

    # must contain korean
    has_korean = re.search(r"[가-힣]", value)

    if not has_korean:
        return ""

    # too short -> invalid
    if len(value) < 20:
        return ""

    return value


def korean_lyrics_score(value: str) -> dict:
    text = re.sub(r"\s+", "", value or "")
    hangul = len(re.findall(r"[가-힣]", text))
    japanese = len(re.findall(r"[\u3040-\u30ff]", text))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-zÀ-ỹ]", text))
    lyric_chars = hangul + japanese + cjk + latin
    hangul_ratio = hangul / lyric_chars if lyric_chars else 0.0
    non_korean_ratio = (japanese + cjk + latin) / lyric_chars if lyric_chars else 1.0
    return {
        "hangul": hangul,
        "japanese": japanese,
        "cjk": cjk,
        "latin": latin,
        "lyric_chars": lyric_chars,
        "hangul_ratio": hangul_ratio,
        "non_korean_ratio": non_korean_ratio,
    }


def is_korean_lyrics(value: str) -> bool:
    score = korean_lyrics_score(value)
    if score["hangul"] < 12:
        return False
    return score["hangul_ratio"] >= 0.35


def sanitize_path(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', " ", str(value or "unknown"))
    value = re.sub(r"\s+", " ", value).strip().strip(".")
    return value[:140] or "unknown"


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def parse_song_id(url: str) -> str:
    return extract_first(url or "", r"songId=(\d+)")

def save_lyrics_file(song, output_dir):
    lyrics_dir = output_dir / "kpop" / "lyrics"
    lyrics_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{song.crawled_song_name}.lrc"

    path = lyrics_dir / filename

    with open(path, "w", encoding="utf-8") as f:
        f.write(song.lyrics or "")

    return path
