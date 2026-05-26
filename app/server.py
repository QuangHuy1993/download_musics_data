from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import urllib.parse
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import DATA_DIR, DEFAULT_OUTPUT_DIR, HOST, PORT, STATIC_DIR
from .db import create_job_db
from .excel_importer import import_input_rows
from .google_sheet import SheetSyncer
from .jsonl_exporter import ensure_delivery_structure
from .utils import clamp_int
from .worker import JobRunner


DB = create_job_db()
SHEET_SYNCER = SheetSyncer(DB)
RUNNER = JobRunner(DB, SHEET_SYNCER)


def extract_browser_cookies(browser: str) -> tuple[bool, str]:
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        return False, "Không tìm thấy yt-dlp trong PATH."

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cookie_file = DATA_DIR / "cookies.txt"
    command = [
        yt_dlp,
        "--cookies-from-browser",
        browser,
        "--cookies",
        str(cookie_file),
        "--skip-download",
        "--simulate",
        "https://www.youtube.com/",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except Exception as error:
        return False, f"Lỗi lấy cookie từ {browser}: {error}"

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        return False, message or f"yt-dlp không lấy được cookie từ {browser}."

    if not cookie_file.exists():
        return False, "yt-dlp chạy xong nhưng chưa tạo data/cookies.txt."

    return True, f"Đã lưu cookie vào {cookie_file}"


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self.write_json({
                "ok": True,
                "ytDlp": bool(shutil.which("yt-dlp")),
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "ffprobe": bool(shutil.which("ffprobe")),
                "node": bool(shutil.which("node")),
                "stats": DB.stats(),
                "runner": RUNNER.state,
            })
            return

        if parsed.path == "/api/status":
            self.write_json({
                "ok": True,
                "stats": DB.stats(),
                "runner": RUNNER.state,
            })
            return

        if parsed.path == "/api/cookies":
            cookie_file = DATA_DIR / "cookies.txt"
            content = ""
            if cookie_file.exists():
                try:
                    content = cookie_file.read_text(encoding="utf-8")
                except Exception as error:
                    content = f"Lỗi đọc file: {str(error)}"
            self.write_json({
                "ok": True,
                "cookies": content,
            })
            return

        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/import_excel":
                self.import_excel()
                return
            if parsed.path == "/api/import_local_excel":
                payload = self.read_json()
                excel_path = Path(payload.get("path")).expanduser()
                if not excel_path.exists():
                    raise FileNotFoundError(f"Không tìm thấy file Excel tại đường dẫn: {excel_path}")
                rows = import_input_rows(excel_path)
                result = DB.import_rows(rows)
                self.write_json({"ok": True, "rows": len(rows), **result, "stats": DB.stats()})
                return
            if parsed.path == "/api/configure_sheet":
                payload = self.read_json()
                SHEET_SYNCER.configure(payload.get("apiUrl", ""), payload.get("token", ""))
                SHEET_SYNCER.start()
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/prepare_output":
                payload = self.read_json()
                output_dir = Path(payload.get("outputDir") or DEFAULT_OUTPUT_DIR).expanduser()
                structure = ensure_delivery_structure(output_dir)
                self.write_json({"ok": True, "structure": structure})
                return
            if parsed.path == "/api/start":
                payload = self.read_json()
                output_dir = Path(payload.get("outputDir") or DEFAULT_OUTPUT_DIR).expanduser()
                ensure_delivery_structure(output_dir)
                workers = clamp_int(payload.get("workers"), default=10, minimum=1, maximum=16)
                max_items = clamp_int(payload.get("maxItems"), default=0, minimum=0, maximum=1000000)
                start_row = clamp_int(payload.get("startRow"), default=0, minimum=0, maximum=10000000)
                end_row = clamp_int(payload.get("endRow"), default=0, minimum=0, maximum=10000000)
                if start_row and end_row and start_row > end_row:
                    raise ValueError("Row bắt đầu phải nhỏ hơn hoặc bằng row kết thúc.")
                RUNNER.start(output_dir, workers, max_items, start_row, end_row)
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/stop":
                RUNNER.stop()
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/log":
                payload = self.read_json()
                message = payload.get("message", "")
                if message:
                    RUNNER.log(message)
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/clear_logs":
                RUNNER.state["logs"] = []
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/reset_db":
                DB.reset_db()
                RUNNER.state["logs"] = []
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/flush_sheet":
                result = SHEET_SYNCER.flush_once()
                self.write_json({"ok": True, "result": result})
                return
            if parsed.path == "/api/save_cookies":
                payload = self.read_json()
                cookies = payload.get("cookies", "").strip()
                cookie_file = DATA_DIR / "cookies.txt"
                if cookies:
                    DATA_DIR.mkdir(parents=True, exist_ok=True)
                    cookie_file.write_text(cookies, encoding="utf-8")
                else:
                    if cookie_file.exists():
                        cookie_file.unlink()
                self.write_json({"ok": True})
                return
            if parsed.path == "/api/extract_browser_cookies":
                payload = self.read_json()
                browser = payload.get("browser", "").lower()
                if browser not in ("chrome", "safari", "edge", "firefox"):
                    raise ValueError("Trình duyệt phải là chrome, edge, safari hoặc firefox")
                success, msg = extract_browser_cookies(browser)
                self.write_json({"ok": success, "message": msg})
                return
            self.write_json({"ok": False, "error": "Endpoint khong ton tai."}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            self.write_json({"ok": False, "error": str(error)}, HTTPStatus.BAD_REQUEST)

    def import_excel(self):
        payload = self.read_json()
        filename = payload.get("filename") or f"input-{uuid.uuid4()}.xlsx"
        raw = base64.b64decode(payload["dataBase64"])
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".xlsx", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            rows = import_input_rows(tmp_path)
            result = DB.import_rows(rows)
            self.write_json({"ok": True, "rows": len(rows), **result, "stats": DB.stats()})
        finally:
            tmp_path.unlink(missing_ok=True)

    def serve_static(self, path: str):
        if path == "/":
            path = "/index.html"
        requested = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(requested).startswith(str(STATIC_DIR.resolve())) or not requested.exists():
            self.write_json({"ok": False, "error": "File khong ton tai."}, HTTPStatus.NOT_FOUND)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }
        data = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(requested.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def write_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def main():
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Melon Music Downloader dang chay: http://{HOST}:{PORT}", flush=True)
    print("Nhan Ctrl+C de dung server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dang dung server...", flush=True)
    finally:
        RUNNER.stop()
        SHEET_SYNCER.stop()
        server.server_close()
        close_db = getattr(DB, "close", None)
        if callable(close_db):
            close_db()
        print("Server da dung.", flush=True)


if __name__ == "__main__":
    main()
