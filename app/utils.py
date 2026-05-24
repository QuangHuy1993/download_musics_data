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
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\u00a0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line)


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
