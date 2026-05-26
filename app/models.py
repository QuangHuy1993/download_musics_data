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

    batch_id: int = 0

    song_id: str = ""

    crawled_song_name: str = ""
    crawled_singer_name: str = ""

    lyrics: str = ""

    audio_path: str = ""

    # path export cho khách
    local_audio_path: str = ""

    lyric_path: str = ""

    duration: float = 0.0

    sample_rate: str = "44.1kHz"

    major_genre: str = "流行"

    sub_genre: str = ""

    album: str = ""

    lyricist: str = ""

    composer: str = ""

    arranger: str = ""

    release_date: str = ""

    like_count: str = ""

    comment_count: str = ""

    language: str = "韩语"

    language_code: str = "ko"

    status: str = "pending"

    attempt_count: int = 0

    error_message: str = ""

    sheet_synced: int = 0

    created_at: str = ""

    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
