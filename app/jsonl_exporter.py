from __future__ import annotations

import json
import os
from pathlib import Path


def clean_jsonl_text(value):
    if isinstance(value, str):
        return (
            value
            .replace("\ufeff", "")
            .replace("\u2028", " ")
            .replace("\u2029", " ")
            .replace("\x00", "")
        )
    if isinstance(value, list):
        return [clean_jsonl_text(item) for item in value]
    if isinstance(value, dict):
        return {key: clean_jsonl_text(item) for key, item in value.items()}
    return value


def append_jsonl(meta_path, data):
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "a", encoding="utf-8") as f:
        json.dump(clean_jsonl_text(data), f, ensure_ascii=False)
        f.write("\n")


def ensure_delivery_structure(output_dir) -> dict:
    output_dir = Path(output_dir)
    audio_dir = output_dir / "kpop" / "audio" / "folder_01"
    meta_dir = output_dir / "meta"
    meta_path = meta_dir / "kpop.jsonl"

    audio_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)
    if not meta_path.exists():
        meta_path.write_text("", encoding="utf-8")

    return {
        "output_dir": str(output_dir),
        "audio_dir": audio_dir.as_posix(),
        "meta_dir": meta_dir.as_posix(),
        "meta_path": meta_path.as_posix(),
    }


def get_unique_key(row):
    song_id = row.get("extra_info", {}).get("song_id")
    if song_id:
        return f"id:{song_id}"
    artist = row.get("extra_info", {}).get("artist", "")
    music_name = row.get("music_name", "")
    return f"name:{str(music_name).strip().lower()}::artist:{str(artist).strip().lower()}"


def write_jsonl(meta_path, new_rows):
    meta_path = Path(meta_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Read existing rows if the file exists
    merged_rows = []
    seen_keys = set()
    
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        key = get_unique_key(row)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            merged_rows.append(row)
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. Merge in new rows (overwriting existing ones with same key)
    rows_dict = {get_unique_key(r): r for r in merged_rows}
    for r in new_rows:
        rows_dict[get_unique_key(r)] = r
        
    final_rows = list(rows_dict.values())
    
    # 3. Sort final_rows by source_row (if exists in extra_info)
    final_rows.sort(
        key=lambda x: (
            int(x.get("extra_info", {}).get("source_row") or 99999999),
            x.get("music_name", "")
        )
    )
    
    # 4. Write back to temp and replace
    tmp_path = meta_path.with_suffix(meta_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for row in final_rows:
            json.dump(clean_jsonl_text(row), f, ensure_ascii=False)
            f.write("\n")
    tmp_path.replace(meta_path)


def song_to_json(song):
    return clean_jsonl_text({
        "stems_info": {},
        "music_name": song.crawled_song_name,
        "oss_path": song.local_audio_path,
        "stems_path": [],
        "duration": round(float(song.duration or 0), 2),
        "major_genre": song.major_genre or "流行",
        "sub_genre": song.sub_genre or "K-Pop",
        "lyric_path": song.lyric_path,
        "sample_rate": song.sample_rate or "44.1kHz",
        "lyric_text": song.lyrics or "",
        "extra_info": {
            "source_row": song.source_row,
            "page_links": song.input_song_url,
            "lyricist": song.lyricist or "",
            "composer": song.composer or "",
            "arranger": song.arranger or "",
            "artist": song.crawled_singer_name,
            "language": song.language or "韩语",
            "language_code": song.language_code or "ko",
            "album": song.album or "",
            "song_id": song.song_id or "",
            "release_date": song.release_date or "",
            "like_count": song.like_count or "",
            "comment_count": song.comment_count or "",
        }
    })
