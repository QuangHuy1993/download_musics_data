from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import fields
from pathlib import Path

from .config import DATA_DIR, DB_PATH
from .models import SongRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobDB:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self):
        with self.connect() as conn:
            conn.execute(
                """
               CREATE TABLE IF NOT EXISTS songs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_row INTEGER NOT NULL,
                    input_song_name TEXT DEFAULT '',
                    input_song_url TEXT NOT NULL,
                    input_singer_name TEXT DEFAULT '',
                    input_lyrics TEXT DEFAULT '',
                    song_id TEXT NOT NULL UNIQUE,
                    crawled_song_name TEXT DEFAULT '',
                    crawled_singer_name TEXT DEFAULT '',
                    lyrics TEXT DEFAULT '',
                    audio_path TEXT DEFAULT '',

                    batch_id INTEGER DEFAULT 0,

                    status TEXT DEFAULT 'pending',
                    attempt_count INTEGER DEFAULT 0,
                    error_message TEXT DEFAULT '',
                    sheet_synced INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
                """
            )
            try:
                conn.execute(
                    "ALTER TABLE songs ADD COLUMN batch_id INTEGER DEFAULT 0"
                )
            except:
                pass
            for ddl in (
                "ALTER TABLE songs ADD COLUMN local_audio_path TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN lyric_path TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN duration REAL DEFAULT 0",
                "ALTER TABLE songs ADD COLUMN sample_rate TEXT DEFAULT '44.1kHz'",
                "ALTER TABLE songs ADD COLUMN major_genre TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN sub_genre TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN album TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN lyricist TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN composer TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN arranger TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN release_date TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN like_count TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN comment_count TEXT DEFAULT ''",
                "ALTER TABLE songs ADD COLUMN language TEXT DEFAULT '韩语'",
                "ALTER TABLE songs ADD COLUMN language_code TEXT DEFAULT 'ko'",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_cache (
                query TEXT PRIMARY KEY,
                video_id TEXT,
                created_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sheet_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    song_id TEXT NOT NULL,
                    queue_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    attempt_count INTEGER DEFAULT 0,
                    error_message TEXT DEFAULT '',
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
                """
            )

    def reset_running(self):
        """Khi khởi động lại chỉ reset các bài đang chạy dở.

        Không reset failed tự động, vì lỗi rate-limit / thiếu lyrics / audio not found
        nếu retry dồn dập sẽ làm YouTube/Melon khóa lâu hơn.
        """
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE songs SET status='pending', updated_at=? WHERE status='running'",
                (now,),
            )

    @staticmethod
    def _row_range_clause(start_row: int = 0, end_row: int = 0) -> tuple[str, list[int]]:
        clauses = []
        params: list[int] = []
        if start_row:
            clauses.append("source_row >= ?")
            params.append(start_row)
        if end_row:
            clauses.append("source_row <= ?")
            params.append(end_row)
        return (" AND " + " AND ".join(clauses), params) if clauses else ("", params)

    def resume_paused_youtube(self, start_row: int = 0, end_row: int = 0) -> int:
        now = utc_now()
        range_clause, range_params = self._row_range_clause(start_row, end_row)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE songs
                SET status='pending', error_message='', updated_at=?
                WHERE status='paused_youtube'
                {range_clause}
                """,
                (now, *range_params),
            )
            return cursor.rowcount or 0

    def reset_db(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM songs")
            conn.execute("DELETE FROM sheet_queue")

    def import_rows(self, rows: list[dict]) -> dict:
        inserted = 0
        skipped = 0
        now = utc_now()
        with self.connect() as conn:
            for row in rows:
                try:
                    conn.execute(
                        """
                        INSERT INTO songs (
                            source_row, input_song_name, input_song_url, input_singer_name,
                            input_lyrics, song_id, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            row["source_row"],
                            row.get("song_name", ""),
                            row["song_url"],
                            row.get("singer_name", ""),
                            row.get("lyrics", ""),
                            row["song_id"],
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    skipped += 1
        return {"inserted": inserted, "skipped": skipped}

    def stats(self) -> dict:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM songs GROUP BY status"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS count FROM songs").fetchone()["count"]
        stats = {row["status"]: row["count"] for row in rows}
        stats["total"] = total
        return stats

    def claim_pending(self, limit: int, start_row: int = 0, end_row: int = 0) -> list[SongRecord]:
        now = utc_now()
        range_clause, range_params = self._row_range_clause(start_row, end_row)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM songs
                WHERE status = 'pending'
                {range_clause}
                ORDER BY source_row ASC
                LIMIT ?
                """,
                (*range_params, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"""
                    UPDATE songs
                    SET status='running', attempt_count=attempt_count+1, updated_at=?
                    WHERE id IN ({placeholders})
                    """,
                    (now, *ids),
                )
        return [self.row_to_song(row) for row in rows]

    def retry_failed_as_pending(
        self,
        start_row: int = 0,
        end_row: int = 0,
        max_attempts: int = 3,
    ) -> int:
        now = utc_now()
        range_clause, range_params = self._row_range_clause(start_row, end_row)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE songs
                SET status='pending', updated_at=?
                WHERE status='failed'
                AND attempt_count < ?
                {range_clause}
                """,
                (now, max_attempts, *range_params),
            )
            return cursor.rowcount or 0

    def mark_done(self, song: SongRecord):
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE songs
                SET crawled_song_name=?, crawled_singer_name=?, lyrics=?,
                    audio_path=?, local_audio_path=?, lyric_path=?, duration=?,
                    sample_rate=?, major_genre=?, sub_genre=?, album=?,
                    lyricist=?, composer=?, arranger=?, release_date=?,
                    like_count=?, comment_count=?,
                    language=?, language_code=?, batch_id=?,
                    status='done', error_message='', updated_at=?
                WHERE id=?
                """,
                (
                    song.crawled_song_name,
                    song.crawled_singer_name,
                    song.lyrics,
                    song.audio_path,
                    song.local_audio_path,
                    song.lyric_path,
                    float(song.duration or 0),
                    song.sample_rate,
                    song.major_genre,
                    song.sub_genre,
                    song.album,
                    song.lyricist,
                    song.composer,
                    song.arranger,
                    song.release_date,
                    song.like_count,
                    song.comment_count,
                    song.language,
                    song.language_code,
                    song.batch_id,
                    now,
                    song.id,
                ),
            )

    def mark_failed(self, song: SongRecord, error_message: str):
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE songs
                SET crawled_song_name=?, crawled_singer_name=?, lyrics=?,
                    status='failed', error_message=?, updated_at=?
                WHERE id=?
                """,
                (
                    song.crawled_song_name,
                    song.crawled_singer_name,
                    song.lyrics,
                    error_message,
                    now,
                    song.id,
                ),
            )

    def mark_skipped(self, song: SongRecord, error_message: str):
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE songs
                SET crawled_song_name=?, crawled_singer_name=?, lyrics=?,
                    status='skipped', error_message=?, updated_at=?
                WHERE id=?
                """,
                (
                    song.crawled_song_name,
                    song.crawled_singer_name,
                    song.lyrics,
                    error_message,
                    now,
                    song.id,
                ),
            )

    def mark_paused_youtube(self, song: SongRecord, error_message: str):
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE songs
                SET crawled_song_name=?, crawled_singer_name=?, lyrics=?,
                    status='paused_youtube', error_message=?, updated_at=?
                WHERE id=?
                """,
                (
                    song.crawled_song_name,
                    song.crawled_singer_name,
                    song.lyrics,
                    error_message,
                    now,
                    song.id,
                ),
            )

    def pause_pending_for_youtube(self, error_message: str, start_row: int = 0, end_row: int = 0):
        now = utc_now()
        range_clause, range_params = self._row_range_clause(start_row, end_row)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE songs
                SET status='paused_youtube', error_message=?, updated_at=?
                WHERE status IN ('pending', 'running')
                {range_clause}
                """,
                (error_message, now, *range_params),
            )

    def enqueue_sheet(self, song: SongRecord, queue_type: str, payload: dict):
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sheet_queue (
                    song_id, queue_type, payload_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (song.song_id, queue_type, json.dumps(payload, ensure_ascii=False), now, now),
            )

    def get_sheet_queue(self, limit: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM sheet_queue
                WHERE status IN ('pending', 'failed')
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def mark_sheet_sent(self, ids: list[int]):
        if not ids:
            return
        now = utc_now()
        with self.connect() as conn:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE sheet_queue SET status='sent', updated_at=? WHERE id IN ({placeholders})",
                (now, *ids),
            )

    def mark_sheet_failed(self, ids: list[int], error: str):
        if not ids:
            return
        now = utc_now()
        with self.connect() as conn:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE sheet_queue
                SET status='failed', attempt_count=attempt_count+1, error_message=?, updated_at=?
                WHERE id IN ({placeholders})
                """,
                (error, now, *ids),
            )

    def get_done_songs(self, batch_id: int | None = None) -> list[SongRecord]:
        clause = "WHERE status='done'"
        params: tuple = ()
        if batch_id is not None:
            clause += " AND batch_id=?"
            params = (batch_id,)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM songs
                {clause}
                ORDER BY source_row ASC
                """,
                params,
            ).fetchall()
        return [self.row_to_song(row) for row in rows]

    def create_new_batch(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT MAX(batch_id) AS current FROM songs").fetchone()
        return int(row["current"] or 0) + 1

    @staticmethod
    def row_to_song(row: sqlite3.Row) -> SongRecord:
        allowed = {field.name for field in fields(SongRecord)}
        return SongRecord(**{key: row[key] for key in row.keys() if key in allowed})

    







def get_cached_video(query):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS youtube_cache (
            query TEXT PRIMARY KEY,
            video_id TEXT,
            created_at TEXT
            )
            """
        )

        row = conn.execute(
            "SELECT video_id FROM youtube_cache WHERE query = ?",
            (query,)
        ).fetchone()

        conn.close()

        if row:
            return row[0]



def save_cached_video(query, video_id):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS youtube_cache (
        query TEXT PRIMARY KEY,
        video_id TEXT,
        created_at TEXT
        )
        """
    )

    conn.execute(
        """
        INSERT OR REPLACE INTO youtube_cache
        (query, video_id, created_at)
        VALUES (?, ?, datetime('now'))
        """,
        (query, video_id)
    )

    conn.commit()
    conn.close()


def create_job_db():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        from .postgres_db import PostgresJobDB

        return PostgresJobDB(database_url)
    return JobDB()

