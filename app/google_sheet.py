from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.request

from .config import SHEET_BATCH_SIZE, SHEET_FLUSH_INTERVAL_SECONDS
from .db import JobDB


class SheetSyncer:
    def __init__(self, db: JobDB):
        self.db = db
        self.api_url = ""
        self.token = ""
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def configure(self, api_url: str, token: str):
        self.api_url = (api_url or "").strip()
        self.token = (token or "").strip()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.flush_once()
            except Exception:
                pass
            self.stop_event.wait(SHEET_FLUSH_INTERVAL_SECONDS)

    def flush_once(self) -> dict:
        if not self.api_url or not self.token:
            return {"sent": 0, "skipped": True}

        rows = self.db.get_sheet_queue(SHEET_BATCH_SIZE)
        if not rows:
            return {"sent": 0}

        by_type: dict[str, list] = {"success": [], "error": []}
        ids_by_type: dict[str, list[int]] = {"success": [], "error": []}
        for row in rows:
            queue_type = row["queue_type"]
            by_type.setdefault(queue_type, []).append(json.loads(row["payload_json"]))
            ids_by_type.setdefault(queue_type, []).append(row["id"])

        sent = 0
        for queue_type, payload_rows in by_type.items():
            if not payload_rows:
                continue
            ids = ids_by_type[queue_type]
            try:
                self.post(queue_type, payload_rows)
                self.db.mark_sheet_sent(ids)
                sent += len(ids)
            except Exception as error:
                self.db.mark_sheet_failed(ids, str(error))
        return {"sent": sent}

    def post(self, queue_type: str, rows: list[dict]):
        body = json.dumps({
            "token": self.token,
            "type": queue_type,
            "rows": rows,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={"Content-Type": "text/plain;charset=utf-8"},
            method="POST",
        )
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("error") or "Google Sheet API error")
