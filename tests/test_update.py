import http.server
import json
import threading
import time

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
        "assets": {},
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


def serve_bytes(body: bytes):
    """假的附件下载地址：返回 body，带 Content-Length。"""

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers["Accept"] == "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/assets/{{id}}"


def test_check_lists_setup_and_portable_assets(monkeypatch):
    assets = [
        {"id": 7, "name": "caifeng-v0.3.0-portable.exe", "size": 100},
        {"id": 8, "name": "caifeng-v0.3.0-setup.exe", "size": 80},
        {"id": 9, "name": "notes.txt", "size": 1},
    ]
    server, url = serve(200, {"tag_name": "v0.3.0", "html_url": "u", "assets": assets})
    monkeypatch.setattr(update, "LATEST", url)
    try:
        r = update.check("0.1.0")
    finally:
        server.shutdown()
    assert r["assets"] == {
        "portable": {"id": 7, "name": "caifeng-v0.3.0-portable.exe", "size": 100},
        "setup": {"id": 8, "name": "caifeng-v0.3.0-setup.exe", "size": 80},
    }


def test_mode_tells_installed_from_portable(tmp_path, monkeypatch):
    monkeypatch.delattr(update.sys, "frozen", raising=False)
    assert update.mode() == ""  # 直接跑代码：没有 exe 可换
    monkeypatch.setattr(update.sys, "frozen", True, raising=False)
    monkeypatch.setattr(update.sys, "executable", str(tmp_path / "caifeng.exe"))
    assert update.mode() == "portable"
    (tmp_path / "unins000.exe").write_bytes(b"")
    assert update.mode() == "setup"
    assert update.target("portable", "caifeng-v0.3.0-portable.exe") == tmp_path / "caifeng-v0.3.0-portable.exe"
    assert update.target("setup", "x-setup.exe").parent.name == "caifeng-update"


def test_download_reports_progress_and_writes_whole_file(tmp_path, monkeypatch):
    body = b"MZ" + bytes(600_000)
    server, url = serve_bytes(body)
    monkeypatch.setattr(update, "ASSET", url)
    seen = []
    try:
        ok = update.download(1, tmp_path / "new.exe", lambda done, total: seen.append((done, total)), lambda: False)
    finally:
        server.shutdown()
    assert ok and (tmp_path / "new.exe").read_bytes() == body
    assert seen[-1] == (len(body), len(body)) and len(seen) > 1
    assert not (tmp_path / "new.exe.part").exists()


def test_download_stopped_leaves_nothing(tmp_path, monkeypatch):
    server, url = serve_bytes(bytes(600_000))
    monkeypatch.setattr(update, "ASSET", url)
    try:
        ok = update.download(1, tmp_path / "new.exe", lambda d, t: None, lambda: True)
    finally:
        server.shutdown()
    assert not ok and list(tmp_path.iterdir()) == []


def test_launch_runs_installer_silently_or_new_portable(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(update.sys, "executable", str(tmp_path / "old.exe"))
    update.launch("setup", tmp_path / "setup.exe")
    update.launch("portable", tmp_path / "new.exe")
    assert started[0] == [str(tmp_path / "setup.exe"), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
    assert started[1] == [str(tmp_path / "new.exe"), update.AFTER_UPDATE, str(tmp_path / "old.exe")]


def make_api(tmp_path, monkeypatch, info):
    from types import SimpleNamespace

    from caifeng.api import Api
    from caifeng.config import ConfigStore

    api = Api(ConfigStore(tmp_path / "config.json"), downloads=tmp_path / "Downloads")
    events, done = [], threading.Event()
    api._emit = lambda site, kind, data: events.append((site, kind, data))
    api.quit = lambda: done.set()
    api._update_info = info
    return api, SimpleNamespace(events=events, done=done)


NEWER = {"ok": True, "newer": True, "latest": "0.3.0", "url": "page", "message": "有新版本 v0.3.0",
         "assets": {"setup": {"id": 8, "name": "caifeng-v0.3.0-setup.exe", "size": 3}}}  # fmt: skip


def test_start_update_downloads_then_launches_and_quits(tmp_path, monkeypatch):
    api, seen = make_api(tmp_path, monkeypatch, NEWER)
    monkeypatch.setattr(update, "mode", lambda: "setup")
    monkeypatch.setattr(update, "target", lambda kind, name: tmp_path / name)

    def fake_download(asset_id, dst, on_progress, should_stop):
        on_progress(3, 3)
        dst.write_bytes(b"MZ!")
        return True

    launched = []
    monkeypatch.setattr(update, "download", fake_download)
    monkeypatch.setattr(update, "launch", lambda kind, f: launched.append((kind, f.name)))
    try:
        assert api.start_update()["ok"]
        assert seen.done.wait(5)
    finally:
        api._shutdown()
    assert [k for _, k, _ in seen.events] == ["progress", "installing"]
    assert launched == [("setup", "caifeng-v0.3.0-setup.exe")]


def test_start_update_reports_errors_and_cancel(tmp_path, monkeypatch):
    api, seen = make_api(tmp_path, monkeypatch, NEWER)
    monkeypatch.setattr(update, "mode", lambda: "setup")
    monkeypatch.setattr(update, "target", lambda kind, name: tmp_path / name)

    def offline(*a):
        raise update.urllib.error.URLError("连不上")

    try:
        monkeypatch.setattr(update, "download", offline)
        api.start_update()
        for _ in range(50):
            if seen.events:
                break
            time.sleep(0.05)
        assert seen.events[-1][1] == "error" and "连不上" in seen.events[-1][2]["message"]
        monkeypatch.setattr(update, "download", lambda *a: False)  # 用户点了取消
        api.start_update()
        for _ in range(50):
            if len(seen.events) > 1:
                break
            time.sleep(0.05)
        assert seen.events[-1][1] == "cancelled" and not seen.done.is_set()
    finally:
        api._shutdown()


def test_start_update_without_exe_opens_page_and_missing_asset(tmp_path, monkeypatch):
    api, seen = make_api(tmp_path, monkeypatch, NEWER)
    opened = []
    monkeypatch.setattr(api, "open_url", lambda url: opened.append(url))
    try:
        monkeypatch.setattr(update, "mode", lambda: "")  # 直接跑代码
        assert api.start_update()["page"] and opened == ["page"]
        monkeypatch.setattr(update, "mode", lambda: "portable")  # 这个版本只有安装包
        r = api.start_update()
        assert not r["ok"] and "便携版" in r["message"]
        api._update_info = {**NEWER, "newer": False, "message": "已经是最新版本"}
        assert api.start_update()["message"] == "已经是最新版本"
    finally:
        api._shutdown()


def test_busy_count_counts_running_work(tmp_path, monkeypatch):
    from types import SimpleNamespace

    api, _ = make_api(tmp_path, monkeypatch, {})
    try:
        assert api.busy_count() == 0
        api._workers["fanqie"] = SimpleNamespace(busy=True, quit=lambda: None, close=lambda: None)
        api._tasks = SimpleNamespace(all=lambda: [{"status": "downloading"}, {"status": "done"}], close=lambda: None)
        assert api.busy_count() == 2  # 番茄在下载 + 一个视频在下载
    finally:
        api._workers.clear()
        api._tasks = None
        api._shutdown()


def test_cleanup_removes_downloaded_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(update.tempfile, "gettempdir", lambda: str(tmp_path))
    folder = update.target("setup", "caifeng-v0.3.0-setup.exe").parent
    folder.mkdir()
    (folder / "caifeng-v0.3.0-setup.exe").write_bytes(b"MZ")
    assert update.cleanup(tries=1)
    assert not folder.exists()
    assert update.cleanup(tries=1)  # 没有也不报错
