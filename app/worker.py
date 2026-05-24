from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import MAX_NORMAL_RETRY_ATTEMPTS
from .db import JobDB, utc_now
from .downloader import YouTubeRateLimitError, download_original_audio
from .google_sheet import SheetSyncer
from .lyrics import enrich_lyrics
from .melon import crawl_song
from .models import SongRecord


class JobRunner:
    def __init__(self, db: JobDB, sheet_syncer: SheetSyncer):
        self.db = db
        self.sheet_syncer = sheet_syncer
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.state = {
            "running": False,
            "startedAt": 0,
            "elapsedSeconds": 0,
            "doneThisRun": 0,
            "ratePerHour": 0,
            "workers": 10,
            "logs": [],
            "pausedReason": "",
        }

    def start(
        self,
        output_dir: Path,
        workers: int = 10,
        max_items: int = 0,
        start_row: int = 0,
        end_row: int = 0,
    ):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Job dang chay.")
        self.stop_event.clear()
        self.state.update({
            "running": True,
            "startedAt": time.time(),
            "elapsedSeconds": 0,
            "doneThisRun": 0,
            "ratePerHour": 0,
            "workers": workers,
            "maxItems": max_items,
            "startRow": start_row,
            "endRow": end_row,
            "logs": [],
            "pausedReason": "",
        })
        self.thread = threading.Thread(
            target=self.run,
            args=(output_dir, workers, max_items, start_row, end_row),
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self, output_dir: Path, workers: int, max_items: int, start_row: int, end_row: int):
        import concurrent.futures
        self.db.reset_running()
        resumed_count = self.db.resume_paused_youtube(start_row, end_row)
        self.sheet_syncer.start()
        limit_text = f", gioi han {max_items} bai" if max_items else ""
        range_text = ""
        if start_row or end_row:
            range_text = f", chi chay row {start_row or 'dau'}-{end_row or 'cuoi'}"
        self.log(f"Bat dau chay voi {workers} worker{limit_text}{range_text}.")
        if resumed_count:
            self.log(f"Da dua {resumed_count} bai paused_youtube ve hang doi pending.")
        
        active_futures = set()
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                while not self.stop_event.is_set():
                    if self.state.get("pausedReason"):
                        break
                    active_count = len(active_futures)
                    free_slots = workers - active_count
                    
                    if max_items:
                        total_submitted = self.state["doneThisRun"] + active_count
                        remaining = max_items - total_submitted
                        free_slots = min(free_slots, remaining)
                        if remaining <= 0 and active_count == 0:
                            break
                    
                    if free_slots > 0:
                        batch = self.db.claim_pending(free_slots, start_row, end_row)
                        if not batch and not active_futures:
                            retry_count = self.db.retry_failed_as_pending(
                                start_row,
                                end_row,
                                MAX_NORMAL_RETRY_ATTEMPTS,
                            )
                            if retry_count:
                                self.log(
                                    f"Da chay het pending, dua {retry_count} bai loi ve cuoi hang doi de thu lai."
                                )
                                batch = self.db.claim_pending(free_slots, start_row, end_row)
                        for song in batch:
                            future = executor.submit(self.process_one, song, output_dir)
                            active_futures.add(future)
                    
                    if not active_futures:
                        break
                    
                    done, active_futures = concurrent.futures.wait(
                        active_futures,
                        return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    
                    for future in done:
                        try:
                            future.result()
                        except YouTubeRateLimitError as e:
                            message = str(e)
                            self.state["pausedReason"] = message
                            self.stop_event.set()
                            self.db.pause_pending_for_youtube(message, start_row, end_row)
                            self.log(message)
                        except Exception as e:
                            self.log(f"He thong loi luong worker: {str(e)}")
                        self.state["doneThisRun"] += 1
                        self.update_rate()

            self.log("Hoan tat job.")
        finally:
            self.state["running"] = False
            self.update_rate()

    def process_one(self, song: SongRecord, output_dir: Path):
        try:
            import random
            time.sleep(random.uniform(0.05, 0.2)) # Stagger threads slightly to avoid concurrent spikes
            self.log(f"Dang xu ly row {song.source_row}: {song.input_song_url}")
            song = crawl_song(song)
            song = enrich_lyrics(song)
            # Lyrics are optional (e.g., for instrumentals), so we do not fail if empty
            song = download_original_audio(song, output_dir)
            self.db.mark_done(song)
            self.db.enqueue_sheet(song, "success", success_payload(song))
            self.log(f"Xong row {song.source_row}: {song.crawled_song_name}")
        except Exception as error:
            message = str(error)
            if isinstance(error, YouTubeRateLimitError):
                self.db.mark_paused_youtube(song, message)
                self.log(message)
                raise
            self.db.mark_failed(song, message)
            self.db.enqueue_sheet(song, "error", error_payload(song, message))
            self.log(f"Loi row {song.source_row}: {message}")

    def update_rate(self):
        elapsed = max(time.time() - self.state["startedAt"], 0.001)
        done = self.state["doneThisRun"]
        self.state["elapsedSeconds"] = round(elapsed, 1)
        self.state["ratePerHour"] = round(done / elapsed * 3600, 1) if done else 0

    def log(self, message: str):
        self.state["logs"].append({"time": time.time(), "message": message})
        self.state["logs"] = self.state["logs"][-200:]


def success_payload(song: SongRecord) -> dict:
    return {
        "source_row": song.source_row,
        "song_name": song.crawled_song_name,
        "singer_name": song.crawled_singer_name,
        "lyrics": song.lyrics,
        "mp3": "",
        "song_url": song.input_song_url,
        "status": "done",
        "updated_at": utc_now(),
    }


def error_payload(song: SongRecord, error_message: str) -> dict:
    return {
        "source_row": song.source_row,
        "song_url": song.input_song_url,
        "input_song_name": song.input_song_name,
        "input_singer_name": song.input_singer_name,
        "error_message": error_message,
        "attempt_count": song.attempt_count,
        "updated_at": utc_now(),
    }
