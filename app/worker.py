from __future__ import annotations

import threading
import time
import gc
import random

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import MAX_NORMAL_RETRY_ATTEMPTS
from .db import JobDB, utc_now
from .downloader import YouTubeRateLimitError, download_original_audio
from .google_sheet import SheetSyncer
from .lyrics import enrich_lyrics
from .melon import crawl_song
from .models import SongRecord
from .jsonl_exporter import ensure_delivery_structure, song_to_json, write_jsonl
from .utils import is_korean_lyrics, korean_lyrics_score

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
            "successThisRun": 0,
            "ratePerHour": 0,
            "workers": 10,
            "logs": [],
            "pausedReason": "",
        }
        self.seen_keys = set()
        self.export_lock = threading.Lock()
        self.state_lock = threading.Lock()

    def start(
        self,
        output_dir: Path,
        workers: int = 3,
        max_items: int = 0,
        start_row: int = 0,
        end_row: int = 0,
    ):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("Job dang chay.")
        self.stop_event.clear()
        self.seen_keys = set()
        batch_id = self.db.create_new_batch()
        self.state.update({
            "running": True,
            "startedAt": time.time(),
            "elapsedSeconds": 0,
            "doneThisRun": 0,
            "successThisRun": 0,
            "ratePerHour": 0,
            "workers": workers,
            "maxItems": max_items,
            "startRow": start_row,
            "endRow": end_row,
            "logs": [],
            "pausedReason": "",
            "batchId": batch_id,
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
        ensure_delivery_structure(output_dir)
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

                            failed_stats = self.db.stats().get("failed", 0)

                            if failed_stats > 0:

                                self.log("Cho cooldown truoc khi retry failed jobs")

                                time.sleep(random.randint(60, 180))

                                retry_count = self.db.retry_failed_as_pending(
                                    start_row,
                                    end_row,
                                    MAX_NORMAL_RETRY_ATTEMPTS,
                                )

                                if retry_count:
                                    self.log(
                                        f"Da chay het pending, dua {retry_count} bai loi ve cuoi hang doi de thu lai."
                                    )

                                    batch = self.db.claim_pending(
                                        free_slots,
                                        start_row,
                                        end_row
                                    )
                            else:

                                self.log("Tat ca bai hat da xu ly xong.")

                                break
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
                        is_success = False
                        try:
                            is_success = future.result()
                            del future
                            if self.state["doneThisRun"] % 100 == 0:
                                gc.collect()

                        except YouTubeRateLimitError as e:
                            message = str(e)
                            self.state["pausedReason"] = message
                            self.stop_event.set()
                            self.db.pause_pending_for_youtube(message, start_row, end_row)
                            self.log(message)
                        except Exception as e:
                            self.log(f"He thong loi luong worker: {str(e)}")
                        
                        with self.state_lock:
                            self.state["doneThisRun"] += 1
                            if is_success:
                                self.state["successThisRun"] += 1
                                current_success = self.state["successThisRun"]
                            else:
                                current_success = None
                        
                        self.update_rate()
                        if is_success and current_success and current_success % 50 == 0:
                            cooldown = random.randint(80, 140)
                            self.log(f"[Global Cooldown] sleeping={cooldown}s (success_count={current_success})")
                            time.sleep(cooldown)

            self.export_sorted_jsonl(output_dir, self.state["batchId"])
            self.log("Hoan tat job.")
        finally:
            self.state["running"] = False
            self.update_rate()

    def export_sorted_jsonl(self, output_dir: Path, batch_id: int):

        with self.export_lock:
            structure = ensure_delivery_structure(output_dir)
            rows = self.db.get_done_songs(None)
            rows.sort(key=lambda x: x.source_row)
            write_jsonl(
                structure["meta_path"],
                [song_to_json(song) for song in rows],
            )
    def process_one(self, song: SongRecord, output_dir: Path):
        try:
            import random
            time.sleep(random.uniform(1.0, 5.0)) # Stagger threads slightly to avoid concurrent spikes
            self.log(f"Dang xu ly row {song.source_row}: {song.input_song_url}")
            song = crawl_song(song)
            dedupe_key = (
                song.crawled_song_name.strip().lower(),
                song.crawled_singer_name.strip().lower()
            )

            if dedupe_key in self.seen_keys:
                raise RuntimeError(f"Skip: duplicate trong cung batch: {song.crawled_song_name}")

            self.seen_keys.add(dedupe_key)
            song = enrich_lyrics(song)

            song.lyrics = (song.lyrics or "").strip()
            if not song.lyrics:
                raise RuntimeError("Skip: khong co lyrics hop le tren Melon/nguon fallback")

            if not is_korean_lyrics(song.lyrics):
                score = korean_lyrics_score(song.lyrics)
                raise RuntimeError(
                    "Skip: lyrics khong phai tieng Han "
                    f"(hangul={score['hangul']}, ratio={score['hangul_ratio']:.2f})"
                )

            song.language = "韩语"
            song.language_code = "ko"
            song = download_original_audio(song, output_dir)

            song.crawled_song_name = (song.crawled_song_name or "").strip()

            song.crawled_singer_name = (song.crawled_singer_name or "").strip()

            song.audio_path = (song.audio_path or "").strip()

            meta_dir = output_dir / "meta"

            if not song.crawled_song_name:
                raise RuntimeError("Thieu ten bai hat")

            if not song.crawled_singer_name:
                raise RuntimeError("Thieu ten ca si")

            if not song.lyrics:
                raise RuntimeError("Lyrics khong hop le")

            if not song.audio_path:
                raise RuntimeError("Thieu audio")

            meta_dir.mkdir(
                parents=True,
                exist_ok=True
            )
            song.batch_id = self.state["batchId"]

            self.db.mark_done(song)
            self.export_sorted_jsonl(output_dir, self.state["batchId"])

            self.db.enqueue_sheet(song, "success", success_payload(song))
            self.log(f"Xong row {song.source_row}: {song.crawled_song_name}")
            return True
        except Exception as error:
            message = str(error)
            if isinstance(error, YouTubeRateLimitError):
                self.db.mark_paused_youtube(song, message)
                self.log(message)
                raise
            if message.startswith("Skip:"):
                self.db.mark_skipped(song, message)
                self.db.enqueue_sheet(song, "error", error_payload(song, message))
                self.log(f"Bo qua row {song.source_row}: {message}")
                return False
            self.db.mark_failed(song, message)
            self.db.enqueue_sheet(song, "error", error_payload(song, message))
            self.log(f"Loi row {song.source_row}: {message}")
            return False

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
