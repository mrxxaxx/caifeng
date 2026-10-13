import http.server
import json
import threading

import pytest

from caifeng import update


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v0.2.0", "0.1.0", True),
        ("0.1.1", "0.1.0", True),
        ("v0.10.0", "0.9.0", True),  # 按数字比，不按字符串
        ("v0.1.0", "0.1.0", False),
        ("v0.2", "0.2.0", False),  # 少写一位也算一样
        ("v0.1.0", "0.2.0", False),
        ("nightly", "0.1.0", False),  # 认不出的不提示
        ("", "0.1.0", False),
    ],
)
def test_newer(latest, current, expected):
    assert update.newer(latest, current) is expected


def serve(status: int, body: dict):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/latest"


def test_check_reports_newer_release(monkeypatch):
    server, url = serve(200, {"tag_name": "v0.3.0", "html_url": "https://github.com/x/releases/tag/v0.3.0"})
    monkeypatch.setattr(update, "LATEST", url)
    try:
        r = update.check("0.1.0")
    finally:
        server.shutdown()
    assert r == {
        "ok": True,
        "newer": True,
        "latest": "0.3.0",
        "url": "https://github.com/x/releases/tag/v0.3.0",
        "message": "有新版本 v0.3.0",
    }


def test_check_without_releases_or_network(monkeypatch):
    server, url = serve(404, {"message": "Not Found"})
    monkeypatch.setattr(update, "LATEST", url)
    try:
        r = update.check("0.1.0")
    finally:
        server.shutdown()
    assert r["ok"] and not r["newer"]  # 还没发过版本
    monkeypatch.setattr(update, "LATEST", "http://127.0.0.1:9/latest")  # 连不上
    r = update.check("0.1.0", timeout=2)
    assert not r["ok"] and not r["newer"] and r["message"].startswith("检查更新失败")
