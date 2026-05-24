from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class SongRecord:
    id: int = 0
    source_row: int = 0
    input_song_name: str = ""
    input_song_url: str = ""
    input_singer_name: str = ""
    input_lyrics: str = ""
    song_id: str = ""
    crawled_song_name: str = ""
    crawled_singer_name: str = ""
    lyrics: str = ""
    audio_path: str = ""
    status: str = "pending"
    attempt_count: int = 0
    error_message: str = ""
    sheet_synced: int = 0
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
