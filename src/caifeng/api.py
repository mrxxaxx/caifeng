"""网页可以调用的 Python 接口：window.pywebview.api.<方法名>(...)。

pywebview 会暴露所有不以下划线开头的方法，所以内部成员一律下划线开头。
后台进度通过 window.caifengEvent(站点, 事件名, 数据) 推回网页。
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

from caifeng import __version__, browser, update
from caifeng.config import ConfigStore, Paths, Settings, _from_dict, output_root, tool_dir
from caifeng.worker import SiteWorker


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def read_clipboard() -> str:
    """读剪贴板里的文字（Windows）。网页自己读剪贴板会弹权限提示，所以由这边读。"""
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _video_platforms() -> list[dict[str, str]]:
    from caifeng.sites import video

    return [{"name": p.NAME, "home": p.HOME} for p in video.PLATFORMS if getattr(p, "HOME", "")]


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except TypeError, ValueError:
        return None


class Api:
    def __init__(self, store: ConfigStore, downloads: Path | None = None):
        self._store = store
        self._downloads = downloads  # 测试时指定，平时向系统查「下载」文件夹
        self._settings = store.load()
        self._window = None  # 由 app.py 在创建窗口后设置
        self._workers: dict[str, SiteWorker] = {}
        self._tasks = None  # 短视频下载列表，第一次用时才建（会起一个下载线程）
        self._parse: dict[str, Any] = {}  # 当前这次解析：id、还剩几个链接、解析出来的作品 {key: (链接, 作品)}

    def _attach(self, window) -> None:
        self._window = window

    def quit(self) -> None:
        """免责声明点了「不同意」：关掉窗口退出。"""
        if self._window is not None:
            self._window.destroy()

    def _shutdown(self) -> None:
        self._window = None  # 窗口没了，不再往页面推事件
        if self._tasks is not None:
            self._tasks.close()
        for w in self._workers.values():
            w.stop()
            w.post("quit")
        for w in self._workers.values():
            w.join(timeout=5)

    def get_state(self) -> dict[str, Any]:
        s = self._settings
        installed = browser.installed()
        current = browser.pick(s.browser, installed)
        return {
            "version": __version__,
            "repo": update.PAGE,  # 关于页的 GitHub、Issues 按钮
            "settings": dataclasses.asdict(s),
            "browsers": [{"key": b.key, "name": b.name} for b in installed],
            "browser": current.key if current else "",
            "browser_links": browser.DOWNLOAD_LINKS,
            "video_platforms": _video_platforms(),  # 「在浏览器打开」下拉里的平台
            "paths": {
                "output_root": str(output_root(s, self._downloads)),
                "data_dir": str(self._paths().root),
                "video": str(tool_dir(s, "video", self._downloads)),
                "fanqie": str(tool_dir(s, "fanqie", self._downloads)),
            },
        }

    def save_settings(self, patch: dict) -> dict[str, Any]:
        """合并网页传来的部分设置，校验后保存，返回最新状态。"""
        merged = _merge(dataclasses.asdict(self._settings), patch or {})
        new = _from_dict(Settings, merged)
        # 填的和默认位置一样就存成空，以后改了总输出目录，各工具的位置能跟着变
        for tool in ("video", "fanqie"):
            override = getattr(new, tool).output_dir
            if override:
                getattr(new, tool).output_dir = ""
                if str(tool_dir(new, tool, self._downloads)) != override:
                    getattr(new, tool).output_dir = override
        if new.output_dir and new.output_dir == str(output_root(Settings(), self._downloads)):
            new.output_dir = ""
        self._settings = new
        self._store.save(new)
        return self.get_state()

    def _paths(self) -> Paths:
        return Paths.from_settings(self._settings)

    def pick_folder(self, initial: str = "") -> str | None:
        if self._window is None:
            return None
        import webview

        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial or "")
        return result[0] if result else None

    def open_folder(self, path: str) -> bool:
        """打开文件夹；给的是文件就打开所在文件夹并选中它。"""
        p = Path(path)
        if not p.exists():
            return False
        if p.is_file():
            import subprocess

            subprocess.Popen(["explorer", "/select,", str(p)])  # noqa: S603, S607
        else:
            os.startfile(p)  # noqa: S606
        return True

    def check_update(self) -> dict[str, Any]:
        """查 GitHub 上的最新版本：{ok, newer, latest, url, message}，见 update.py。"""
        return update.check()

    def open_url(self, url: str) -> None:
        import webbrowser

        webbrowser.open(url)

    def _emit(self, site: str, kind: str, data: dict) -> None:
        if self._window is not None:
            payload = json.dumps([site, kind, data], ensure_ascii=False)
            self._window.evaluate_js(f"window.caifengEvent && window.caifengEvent(...{payload})")

    def _worker(self, site: str) -> SiteWorker:
        if site not in self._workers:
            w = SiteWorker(
                site,
                emit=lambda kind, data, site=site: self._emit(site, kind, data),
                browser_key=lambda: self._settings.browser,
                profile_root=lambda: self._paths().root / "profiles",
            )
            w.start()
            self._workers[site] = w
        return self._workers[site]

    def _profile_exists(self, site: str) -> bool:
        b = browser.pick(self._settings.browser)
        return bool(b) and self._paths().profile(b.key, site).exists()

    # ---- 站点通用：site 是 "fanqie" 或 "video" ----
    def check_login(self, site: str) -> None:
        """后台检测登录。从没在这个浏览器里登录过（没有 profile）就直接报未登录，不必启动浏览器。"""
        if not self._profile_exists(site):
            self._emit(site, "login", {"ok": False})
            return
        self._worker(site).post("check_login")

    def open_site(self, site: str, url: str = "") -> None:
        """在采风的浏览器里打开网站；url 给了就打开它（短视频：选哪个平台打开哪个）。"""
        self._worker(site).post("open_site", url=url)

    def grab(self, site: str) -> None:
        self._worker(site).post("grab")

    def stop(self, site: str) -> None:
        if site in self._workers:
            self._workers[site].stop()

    # ---- 短视频：下载列表（见 videotasks.py）----
    def _video_list(self):
        if self._tasks is None:
            from caifeng.videotasks import TaskList

            def resolve(task_id: str, link: str) -> None:
                tl = self._tasks
                self._worker("video").post(
                    "resolve",
                    link=link,
                    on_done=lambda items: tl.resolved(task_id, items),
                    on_fail=lambda error: tl.failed(task_id, error),
                    nomark=self._settings.video.nomark,
                )

            self._tasks = TaskList(
                emit=lambda kind, data: self._emit("video", kind, data),
                resolve=resolve,
                out_root=lambda: tool_dir(self._settings, "video", self._downloads),
                store=self._paths().root / "video_tasks.json",
            )
        return self._tasks

    def video_tasks(self) -> list[dict]:
        return self._video_list().all()

    def video_add(self, text: str) -> dict[str, Any]:
        """解析粘贴内容里的每个链接，作品通过 parse_start/step/item/error/done 事件推给解析对话框。
        返回 {ok, n, message}，n 是链接个数；界面挑好后调 video_download。"""
        import uuid

        from caifeng.sites import shipinhao, video, xiaohongshu

        links = video.links_in(text or "")
        if not links:
            names = "、".join(p.NAME for p in video.PLATFORMS if p is not shipinhao)
            return {"ok": False, "n": 0, "message": f"没有找到认得的链接：目前支持{names}"}
        pid = uuid.uuid4().hex[:8]
        # 取消或开始新的一次后，这次的回调都不再算数
        session = {"id": pid, "n": len(links), "left": len(links), "items": {}}
        self._parse = session
        self._emit("video", "parse_start", {"n": len(links)})

        def current() -> bool:
            return self._parse is session

        def finish_one() -> None:
            session["left"] -= 1
            if session["left"] == 0 and current():
                self._emit("video", "parse_done", {})

        def post(i: int, link: str) -> None:
            def start() -> bool:
                if current():
                    self._emit("video", "parse_step", {"i": i, "n": len(links), "link": link})
                return current()

            def done(items) -> None:
                if not current():
                    return
                if not items:
                    self._emit("video", "parse_error", {"link": link, "error": "没有解析出视频"})
                for item in items:
                    key = uuid.uuid4().hex[:10]
                    session["items"][key] = (link, item)
                    row = {"key": key, "platform": item.platform, "title": item.title, "author": item.author,
                           "cover": item.cover, "duration": item.duration, "quality": item.quality,
                           "images": item.thumbs or item.images, "text": item.text[:200]}  # fmt: skip
                    self._emit("video", "parse_item", row)
                finish_one()

            def fail(error) -> None:
                if current():
                    row = {"link": link, "error": error}
                    if error.startswith(xiaohongshu.LOGIN_WALL):  # 让用户选：登录，或者换个游客身份
                        row["wall"] = xiaohongshu.NAME
                    self._emit("video", "parse_error", row)
                    finish_one()

            self._worker("video").post(
                "resolve", link=link, on_done=done, on_fail=fail, on_start=start, nomark=self._settings.video.nomark
            )

        session["post"] = post
        for i, link in enumerate(links, 1):
            post(i, link)
        return {"ok": True, "n": len(links), "message": ""}

    def video_reparse(self, link: str, new_guest: bool = False) -> None:
        """小红书要求登录时，在这次解析里重新解析这个链接；new_guest 先清掉小红书的 cookie。"""
        from caifeng.sites import xiaohongshu

        session = self._parse
        if not session.get("post"):
            return
        session["left"] += 1
        if new_guest:
            self._worker("video").post("new_guest", domain=xiaohongshu.COOKIE_DOMAIN)
        session["post"](session["n"], link)

    def video_parse_cancel(self) -> None:
        """解析对话框点了取消：还没解析的链接不再解析，已经解析出来的丢掉。"""
        self._parse = {}

    def video_download(self, keys: list[str], picks: dict[str, list[int]] | None = None) -> None:
        """解析对话框里选中的作品，加进下载列表开始排队。picks：图文作品选了哪几张图（下标），没给就全要。"""
        items = self._parse.get("items") or {}
        chosen = []
        for k in keys:
            if k not in items:
                continue
            link, item = items[k]
            idx = None
            if picks and k in picks and item.images:
                idx = [i for i in picks[k] if 0 <= i < len(item.images)]
                if not idx:
                    continue
                if len(idx) == len(item.images):
                    idx = None  # 全选就当没挑
            chosen.append((link, item, idx))
        self._video_list().add_items(chosen)
        self._parse = {}

    def video_add_clipboard(self) -> dict[str, Any]:
        """解析剪贴板里的内容（复制了分享口令，回来点一下就行）。"""
        text = read_clipboard()
        if not text.strip():
            return {"ok": False, "n": 0, "message": "剪贴板是空的：先复制分享口令或链接"}
        return self.video_add(text)

    def video_task(self, action: str, task_id: str) -> None:
        """action：stop、retry、remove（不删文件）、new_guest（清掉小红书 cookie 再重试）。"""
        from caifeng.sites import xiaohongshu

        tl = self._video_list()
        if action == "new_guest":
            self._worker("video").post("new_guest", domain=xiaohongshu.COOKIE_DOMAIN)  # 排在重新解析前面
            action = "retry"
        {"stop": tl.stop, "retry": tl.retry, "remove": tl.remove}[action](task_id)

    def video_clear_done(self) -> None:
        self._video_list().clear_done()

    def open_file(self, path: str) -> bool:
        """用系统默认程序打开（下好的视频）。"""
        p = Path(path)
        if not p.exists():
            return False
        os.startfile(p)  # noqa: S606
        return True

    def fanqie_download(self, params: dict) -> None:
        """params：book_id（可空）、kind；短剧漫剧另有 start、end（集数，空表示全部 / 到最后一集）。"""
        s = self._settings.fanqie
        kind = params.get("kind") or s.kind
        opts: dict[str, Any] = {"include_paid": s.scope == "all"}
        if kind != "novel":
            opts = {
                "start": _int_or_none(params.get("start")),
                "end": _int_or_none(params.get("end")),
                "save_mode": s.save_mode,
            }
        self._worker("fanqie").post(
            "download",
            kind=kind,
            book_id=str(params.get("book_id") or "").strip(),
            out_root=str(tool_dir(self._settings, "fanqie", self._downloads)),
            delay=s.delay,
            **opts,
        )
