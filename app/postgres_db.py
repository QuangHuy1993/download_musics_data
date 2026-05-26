from __future__ import annotations

import json
import os
import socket
import atexit
from contextlib import contextmanager
from dataclasses import fields

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .db import utc_now
from .models import SongRecord


SONG_FIELDS = {field.name for field in fields(SongRecord)}


class PostgresJobDB:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.worker_id = os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
        pool_size = int(os.environ.get("DB_POOL_SIZE", "8"))
        self.lock_timeout_minutes = int(os.environ.get("JOB_LOCK_TIMEOUT_MINUTES", "30"))
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=max(2, pool_size),
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=True,
        )
        atexit.register(self.close)
        self.init()

    def close(self):
        try:
            self.pool.close()
        except Exception:
            pass

    @contextmanager
    def connect(self):
        with self.pool.connection() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def init(self):
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS songs (
                    id BIGSERIAL PRIMARY KEY,
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
                    local_audio_path TEXT DEFAULT '',
                    lyric_path TEXT DEFAULT '',
                    duration DOUBLE PRECISION DEFAULT 0,
                    sample_rate TEXT DEFAULT '44.1kHz',
                    major_genre TEXT DEFAULT '',
                    sub_genre TEXT DEFAULT '',
                    album TEXT DEFAULT '',
                    lyricist TEXT DEFAULT '',
                    composer TEXT DEFAULT '',
                    arranger TEXT DEFAULT '',
                    release_date TEXT DEFAULT '',
                    like_count TEXT DEFAULT '',
                    comment_count TEXT DEFAULT '',
                    language TEXT DEFAULT '韩语',
                    language_code TEXT DEFAULT 'ko',
                    batch_id INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    worker_id TEXT DEFAULT '',
                    locked_at TIMESTAMPTZ,
                    attempt_count INTEGER DEFAULT 0,
                    error_message TEXT DEFAULT '',
                    sheet_synced INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sheet_queue (
                    id BIGSERIAL PRIMARY KEY,
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS youtube_cache (
                    query TEXT PRIMARY KEY,
                    video_id TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS songs_song_id_uidx ON songs(song_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS songs_status_source_row_idx ON songs(status, source_row)")
            conn.execute("CREATE INDEX IF NOT EXISTS songs_worker_status_idx ON songs(worker_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS songs_locked_at_idx ON songs(locked_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS songs_updated_at_idx ON songs(updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS sheet_queue_status_id_idx ON sheet_queue(status, id)")
            conn.execute("CREATE SEQUENCE IF NOT EXISTS job_batch_seq")

    @staticmethod
    def _row_range_sql(start_row: int = 0, end_row: int = 0) -> tuple[str, list[int]]:
        clauses = []
        params: list[int] = []
        if start_row:
            clauses.append("source_row >= %s")
            params.append(start_row)
        if end_row:
            clauses.append("source_row <= %s")
            params.append(end_row)
        return (" AND " + " AND ".join(clauses), params) if clauses else ("", params)

    def reset_running(self):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE songs
                SET status='pending', worker_id='', locked_at=NULL, updated_at=%s
                WHERE status='running'
                AND locked_at < now() - (%s || ' minutes')::interval
                """,
                (utc_now(), self.lock_timeout_minutes),
            )

    def resume_paused_youtube(self, start_row: int = 0, end_row: int = 0) -> int:
        clause, params = self._row_range_sql(start_row, end_row)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE songs
                SET status='pending', error_message='', worker_id='', locked_at=NULL, updated_at=%s
                WHERE status='paused_youtube'
                {clause}
                """,
                (utc_now(), *params),
            )
            return cursor.rowcount or 0

    def reset_db(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM songs")
            conn.execute("DELETE FROM sheet_queue")

    def import_rows(self, rows: list[dict]) -> dict:
        if not rows:
            return {"inserted": 0, "skipped": 0}

        inserted = 0
        now = utc_now()
        chunk_size = int(os.environ.get("DB_IMPORT_CHUNK_SIZE", "500"))
        with self.connect() as conn:
            for start in range(0, len(rows), chunk_size):
                chunk = rows[start:start + chunk_size]
                placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,'pending',%s,%s)"] * len(chunk))
                params = []
                for row in chunk:
                    params.extend((
                        row["source_row"],
                        row.get("song_name", ""),
                        row["song_url"],
                        row.get("singer_name", ""),
                        row.get("lyrics", ""),
                        row["song_id"],
                        now,
                        now,
                    ))

                result = conn.execute(
                    f"""
                    INSERT INTO songs (
                        source_row, input_song_name, input_song_url, input_singer_name,
                        input_lyrics, song_id, status, created_at, updated_at
                    ) VALUES {placeholders}
                    ON CONFLICT (song_id) DO NOTHING
                    RETURNING id
                    """,
                    params,
                ).fetchall()
                inserted += len(result)
        skipped = len(rows) - inserted
        return {"inserted": inserted, "skipped": skipped}

    def stats(self) -> dict:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM songs GROUP BY status"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS count FROM songs").fetchone()["count"]
        result = {row["status"]: row["count"] for row in rows}
        result["total"] = total
        result["workerId"] = self.worker_id
        return result

    def claim_pending(self, limit: int, start_row: int = 0, end_row: int = 0) -> list[SongRecord]:
        if limit <= 0:
            return []
        clause, params = self._row_range_sql(start_row, end_row)
        now = utc_now()
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH picked AS (
                    SELECT id
                    FROM songs
                    WHERE status='pending'
                    {clause}
                    ORDER BY source_row ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE songs AS s
                SET status='running',
                    worker_id=%s,
                    locked_at=now(),
                    attempt_count=s.attempt_count + 1,
                    updated_at=%s
                FROM picked
                WHERE s.id = picked.id
                RETURNING s.*
                """,
                (*params, limit, self.worker_id, now),
            ).fetchall()
        return [self.row_to_song(row) for row in rows]

    def retry_failed_as_pending(
        self,
        start_row: int = 0,
        end_row: int = 0,
        max_attempts: int = 3,
    ) -> int:
        clause, params = self._row_range_sql(start_row, end_row)
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                UPDATE songs
                SET status='pending', worker_id='', locked_at=NULL, updated_at=%s
                WHERE status='failed'
                AND attempt_count < %s
                {clause}
                """,
                (utc_now(), max_attempts, *params),
            )
            return cursor.rowcount or 0

    def mark_done(self, song: SongRecord):
        self._update_song_terminal(song, "done", "")

    def mark_failed(self, song: SongRecord, error_message: str):
        self._update_song_terminal(song, "failed", error_message)

    def mark_skipped(self, song: SongRecord, error_message: str):
        self._update_song_terminal(song, "skipped", error_message)

    def mark_paused_youtube(self, song: SongRecord, error_message: str):
        self._update_song_terminal(song, "paused_youtube", error_message)

    def _update_song_terminal(self, song: SongRecord, status: str, error_message: str):
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE songs
                SET crawled_song_name=%s, crawled_singer_name=%s, lyrics=%s,
                    audio_path=%s, local_audio_path=%s, lyric_path=%s, duration=%s,
                    sample_rate=%s, major_genre=%s, sub_genre=%s, album=%s,
                    lyricist=%s, composer=%s, arranger=%s, release_date=%s,
                    like_count=%s, comment_count=%s,
                    language=%s, language_code=%s, batch_id=%s,
                    status=%s, error_message=%s, worker_id='', locked_at=NULL, updated_at=%s
                WHERE id=%s
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
                    status,
                    error_message,
                    utc_now(),
                    song.id,
                ),
            )

    def pause_pending_for_youtube(self, error_message: str, start_row: int = 0, end_row: int = 0):
        clause, params = self._row_range_sql(start_row, end_row)
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE songs
                SET status='paused_youtube', error_message=%s, worker_id='', locked_at=NULL, updated_at=%s
                WHERE status IN ('pending', 'running')
                {clause}
                """,
                (error_message, utc_now(), *params),
            )

    def enqueue_sheet(self, song: SongRecord, queue_type: str, payload: dict):
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sheet_queue (
                    song_id, queue_type, payload_json, status, created_at, updated_at
                ) VALUES (%s, %s, %s, 'pending', %s, %s)
                """,
                (song.song_id, queue_type, json.dumps(payload, ensure_ascii=False), now, now),
            )

    def get_sheet_queue(self, limit: int) -> list[dict]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM sheet_queue
                WHERE status IN ('pending', 'failed')
                ORDER BY id ASC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def mark_sheet_sent(self, ids: list[int]):
        if not ids:
            return
        with self.connect() as conn:
            conn.execute(
                "UPDATE sheet_queue SET status='sent', updated_at=%s WHERE id = ANY(%s)",
                (utc_now(), ids),
            )

    def mark_sheet_failed(self, ids: list[int], error: str):
        if not ids:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sheet_queue
                SET status='failed', attempt_count=attempt_count+1, error_message=%s, updated_at=%s
                WHERE id = ANY(%s)
                """,
                (error, utc_now(), ids),
            )

    def get_done_songs(self, batch_id: int | None = None) -> list[SongRecord]:
        clause = "WHERE status='done'"
        params: tuple = ()
        if batch_id is not None:
            clause += " AND batch_id=%s"
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
            row = conn.execute("SELECT nextval('job_batch_seq') AS next_batch").fetchone()
        return int(row["next_batch"])

    @staticmethod
    def row_to_song(row: dict) -> SongRecord:
        return SongRecord(**{key: row.get(key) for key in SONG_FIELDS if key in row})
