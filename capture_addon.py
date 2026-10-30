"""
mitmproxy addon: saves every request/response (minus obvious Google/analytics noise)
as a readable JSON file under ./captures, plus a one-line summary in captures/index.jsonl.
Used by run_capture.bat.
"""
import json
import re
import time
from pathlib import Path

from mitmproxy import http

OUT = Path(__file__).parent / "captures"

NOISE_HOSTS = (
    "google-analytics", "googletagmanager", "doubleclick", "app-measurement",
    "crashlytics", "firebaselogging", "firebase-settings", "play.googleapis.com",
    "android.clients.google.com", "connectivitycheck", "gstatic.com",
    "googleadservices", "facebook.com", "graph.facebook", "appsflyer", "branch.io",
)
BINARY_TYPES = ("image/", "font/", "video/", "audio/", "application/octet-stream")


def _body(msg):
    if not msg.raw_content:
        return None
    ctype = msg.headers.get("content-type", "")
    if ctype.startswith(BINARY_TYPES):
        return f"<{ctype}, {len(msg.raw_content)} bytes>"
    text = msg.get_text(strict=False)
    if text is None:
        return f"<binary, {len(msg.raw_content)} bytes>"
    try:
        return json.loads(text)
    except ValueError:
        return text


def _slug(s, n=60):
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:n] or "root"


class Capture:
    def __init__(self):
        OUT.mkdir(exist_ok=True)
        self.n = len(list(OUT.glob("*.json")))

    def response(self, flow: http.HTTPFlow):
        req, res = flow.request, flow.response
        host = req.pretty_host
        if any(h in host for h in NOISE_HOSTS):
            return

        self.n += 1
        name = f"{self.n:04d}_{req.method}_{_slug(host, 40)}_{_slug(req.path.split('?')[0])}.json"
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "method": req.method,
            "url": req.pretty_url,
            "request_headers": dict(req.headers),
            "request_body": _body(req),
            "status": res.status_code,
            "response_headers": dict(res.headers),
            "response_body": _body(res),
        }
        (OUT / name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = {
            "file": name, "time": record["time"], "method": req.method, "url": req.pretty_url,
            "status": res.status_code, "type": res.headers.get("content-type", ""),
            "bytes": len(res.raw_content or b""),
        }
        with (OUT / "index.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        print(f"[saved] {res.status_code} {req.method} {req.pretty_url[:120]}")


addons = [Capture()]
