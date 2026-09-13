#!/usr/bin/env python
"""Serve archived A-share daily reports to devices on the local network."""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = PROJECT_ROOT / "web"
REPORT_DIR = PROJECT_ROOT / "data" / "reports"
HOST = os.getenv("FINANCE_REPORT_HOST", "0.0.0.0")
PORT = int(os.getenv("FINANCE_REPORT_PORT", "8766"))
REPORT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
SUBSCRIBERS: set[queue.Queue] = set()
SUBSCRIBERS_LOCK = threading.Lock()


def publish_report_event(report_date: str):
    message = json.dumps({"date": report_date}, ensure_ascii=False)
    with SUBSCRIBERS_LOCK:
        subscribers = list(SUBSCRIBERS)
    for subscriber in subscribers:
        subscriber.put(message)


def list_reports():
    if not REPORT_DIR.exists():
        return []
    reports = []
    for path in REPORT_DIR.glob("*.md"):
        if not REPORT_NAME.fullmatch(path.name):
            continue
        stat = path.stat()
        reports.append({
            "date": path.stem,
            "updated_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            "size": stat.st_size,
        })
    return sorted(reports, key=lambda item: item["date"], reverse=True)


def read_report(filename: str):
    if not REPORT_NAME.fullmatch(filename):
        raise ValueError("日报文件名无效。")
    path = REPORT_DIR / filename
    if not path.exists():
        return None
    stat = path.stat()
    return {
        "available": True,
        "date": path.stem,
        "updated_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "markdown": path.read_text(encoding="utf-8"),
    }


class ReportHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def send_bytes(self, status: int, body: bytes, content_type: str, cache_control: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: dict | list):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/health":
                return self.send_json(200, {"status": "ok", "report_count": len(list_reports())})
            if path == "/api/reports":
                return self.send_json(200, {"reports": list_reports()})
            if path == "/api/events":
                return self.stream_events()
            if path == "/api/reports/latest":
                reports = list_reports()
                if not reports:
                    return self.send_json(200, {"available": False, "markdown": ""})
                return self.send_json(200, read_report(reports[0]["date"] + ".md"))
            if path.startswith("/api/reports/"):
                filename = path.removeprefix("/api/reports/") + ".md"
                report = read_report(filename)
                return self.send_json(200, report) if report else self.send_json(404, {"available": False, "error": "未找到该日报。"})

            asset = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            if asset not in {"index.html", "styles.css", "app.js"}:
                return self.send_json(404, {"error": "Not found"})
            target = WEB_ROOT / asset
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self.send_bytes(200, target.read_bytes(), content_type, "no-cache")
        except (OSError, ValueError) as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path != "/api/publish":
            return self.send_json(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 10_000:
                raise ValueError("请求体大小无效。")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            report_date = str(payload.get("date", ""))
            if not REPORT_NAME.fullmatch(report_date + ".md") or not (REPORT_DIR / f"{report_date}.md").exists():
                raise ValueError("日报日期无效或文件不存在。")
            publish_report_event(report_date)
            self.send_json(200, {"published": True, "date": report_date})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})

    def stream_events(self):
        subscriber: queue.Queue = queue.Queue()
        with SUBSCRIBERS_LOCK:
            SUBSCRIBERS.add(subscriber)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(b"event: ready\ndata: {}\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = subscriber.get(timeout=20)
                    chunk = f"event: report\ndata: {message}\n\n".encode("utf-8")
                except queue.Empty:
                    chunk = b": keep-alive\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with SUBSCRIBERS_LOCK:
                SUBSCRIBERS.discard(subscriber)


if __name__ == "__main__":
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"A-share report site: http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), ReportHandler).serve_forever()
