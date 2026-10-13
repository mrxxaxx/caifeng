"""站点后台线程：独占一个浏览器（Playwright 同步 API 只能在创建它的线程里用），按顺序执行界面发来的命令。"""

from __future__ import annotations

import contextlib
import queue
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from caifeng import browser as browsers
from caifeng.sites import fanqie, video

RESOLVE_GAP = 2.0  # 解析链接之间至少隔几秒

Emit = Callable[[str, dict[str, Any]], None]
LOGIN_TIMEOUT = 600  # 等待登录最长 10 分钟
SITES = {"fanqie": fanqie, "video": video}  # 每个站点模块提供 HOME、LOGIN_REQUIRED 和 is_logged_in(page)
NAMES = {"fanqie": "番茄达人中心", "video": "短视频"}


def transfer_reporter(emit: Emit) -> Callable[[int, int], None]:
    """把每块数据的回调节流成每 0.3 秒一次的 transfer 事件，顺便算出下载速度（最近约 2 秒的平均）。"""
    samples: list[tuple[float, int]] = []  # (时间, 累计字节)
    state = {"total": 0, "last_file": 0, "last_emit": 0.0}

    def report(got: int, size: int) -> None:
        now = time.monotonic()
        if got < state["last_file"]:  # 换了一个文件
            state["total"] += state["last_file"]
        state["last_file"] = got
        samples.append((now, state["total"] + got))
        while len(samples) > 2 and now - samples[0][0] > 2:
            samples.pop(0)
        if now - state["last_emit"] < 0.3 and got != size:
            return
        state["last_emit"] = now
        span = now - samples[0][0]
        speed = (samples[-1][1] - samples[0][1]) / span if span > 0 else 0
        emit("transfer", {"got": got, "size": size, "speed": speed})

    return report


class SiteWorker(threading.Thread):
    def __init__(self, site: str, emit: Emit, browser_key: Callable[[], str], profile_root: Callable[[], Path]):
        super().__init__(daemon=True, name=f"worker-{site}")
        self.site = site
        self.mod = SITES[site]
        self._direct = getattr(self.mod, "DIRECT", False)  # 不走系统代理（短视频）
        self.emit = emit
        self._browser_key = browser_key  # 每次启动浏览器时取最新设置
        self._profile_root = profile_root
        self._cmds: queue.Queue = queue.Queue()
        self._stop = threading.Event()  # 停止当前下载 / 等待登录
        self._last_resolve = 0.0  # 上次开始解析链接的时间（time.monotonic）
        self._pw = None
        self._ctx = None
        self._headless = True
        self._window = False  # 界面知道的「浏览器窗口开着」，变了才通知
        self._dl = None  # 短视频：用户开着窗口时另起的后台浏览器 (browser, context, page)

    # ---- 给 Api 调用（任意线程） ----
    def post(self, cmd: str, **kw) -> None:
        if cmd != "quit":
            self._stop.clear()
        self._cmds.put((cmd, kw))

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            self._pw = pw
            while True:
                try:
                    cmd, kw = self._cmds.get(timeout=1.5)
                except queue.Empty:
                    # 闲着时看一眼窗口还在不在：用户点 × 关掉窗口，要和浏览器通信一次才知道
                    if self._ctx and not self._headless:
                        self._check_alive()
                    self._report_window()
                    continue
                if cmd == "quit":
                    self._close()
                    self._close_dl()
                    return
                self.emit("busy", {"on": True})
                try:
                    getattr(self, "cmd_" + cmd)(**kw)
                except Exception as e:  # noqa: BLE001  任何错误都报给界面，线程继续活着
                    self.emit("error", {"message": str(e) or e.__class__.__name__})
                    if sys.stderr:  # 打包后的 exe 没有控制台，sys.stderr 是 None
                        traceback.print_exc()
                finally:
                    self.emit("busy", {"on": False})
                    self._report_window()

    def _report_window(self) -> None:
        """有窗口的浏览器开着没有，变了就告诉界面（短视频的「解析当前页」要靠它）。"""
        open_ = bool(self._ctx) and not self._headless
        if open_ != self._window:
            self._window = open_
            self.emit("browser", {"open": open_})

    def _page(self, headless: bool):
        """保证浏览器开着；要显示窗口而现在是无头的，就重开成有窗口的（反过来不必，窗口开着照样能用）。"""
        self._check_alive()
        if self._ctx and not headless and self._headless:
            self._close()
        if not self._ctx:
            b = browsers.pick(self._browser_key())
            if b is None:
                raise RuntimeError("没有找到 Edge 或 Chrome，请先安装其中一个")
            profile = self._profile_root() / b.key / self.site
            self._ctx = browsers.launch(self._pw, b, profile, headless=headless, direct=self._direct)
            self._ctx.on("close", lambda _: setattr(self, "_ctx", None))  # 用户关掉了窗口
            for script in getattr(self.mod, "INIT_SCRIPTS", ()):  # 比如快手推荐页记下正在播的是哪个作品
                self._ctx.add_init_script(script)
            self._headless = headless
        pages = [p for p in self._ctx.pages if not p.is_closed()]
        return pages[0] if pages else self._ctx.new_page()

    def _check_alive(self) -> None:
        """用户点 × 关掉窗口后要等下次和浏览器通信才知道，所以先往返一次；关了就当没开。"""
        if self._ctx:
            try:
                self._ctx.cookies()
            except Exception:  # noqa: BLE001  浏览器已经关了
                self._close()

    def _close(self) -> None:
        if self._ctx:
            with contextlib.suppress(Exception):
                self._ctx.close()
        self._ctx = None

    def _close_dl(self) -> None:
        if self._dl:
            with contextlib.suppress(Exception):
                self._dl[0].close()
        self._dl = None

    def _pages(self) -> list:
        """当前打开的标签页。先在每页做一次往返，让 Playwright 处理积压事件（用户的跳转、新开的标签）。"""
        self._check_alive()
        if not self._ctx:
            return []
        for p in list(self._ctx.pages):
            with contextlib.suppress(Exception):
                p.evaluate("1")
        return [p for p in self._ctx.pages if not p.is_closed()] if self._ctx else []

    # ---- 命令：所有站点通用 ----
    def cmd_check_login(self) -> None:
        page = self._page(headless=self._headless if self._ctx else True)
        self.emit("login", {"ok": self.mod.is_logged_in(page)})

    def cmd_open_site(self, url: str = "") -> None:
        """弹出窗口打开网站。要登录的网站（番茄）没登录就等用户登录，登上了自动报告；短视频打开就行。"""
        page = self._page(headless=False)
        page.goto(url or self.mod.HOME, wait_until="domcontentloaded", timeout=60_000)
        page.bring_to_front()
        page.wait_for_timeout(1500)
        if self.mod.is_logged_in(page) or not self.mod.LOGIN_REQUIRED:
            self.emit("login", {"ok": self.mod.is_logged_in(page)})
            return
        self.emit("login", {"ok": False, "waiting": True})
        deadline = time.time() + LOGIN_TIMEOUT
        while time.time() < deadline and not self._stop.is_set() and self._ctx:
            time.sleep(2)
            pages = self._pages()
            if pages and self.mod.is_logged_in(pages[-1]):
                self.emit("login", {"ok": True})
                return
        self.emit("login", {"ok": False})

    def cmd_grab(self) -> None:
        """从浏览器打开的标签页里找当前的书/剧/作品（新打开的标签在后面，从后往前找）。"""
        urls = [p.url for p in self._pages()]
        if not urls:
            what = "一本书的详情页" if self.site == "fanqie" else "一个视频"
            self.emit(
                "grab", {"id": None, "message": f"浏览器没有打开。先点右上角打开{NAMES[self.site]}，在窗口里打开{what}"}
            )
            return
        if self.site == "fanqie":
            url = next((u for u in reversed(urls) if fanqie.book_id_from_url(u)), None)
            if url:
                self.emit("grab", {"id": fanqie.book_id_from_url(url), "kind": fanqie.kind_from_url(url)})
            else:
                self.emit("grab", {"id": None, "message": "打开的标签页里没有书籍详情页（网址里要有 book_id）"})
        else:
            link = self._video_current()
            if link:
                self.emit("grab", {"id": link})
            else:
                self.emit("grab", {"id": None, "message": "浏览器里没有正在看的视频：打开或播放一个视频再试"})

    def _video_current(self) -> str | None:
        """浏览器里当前在看的作品链接。抖音推荐页刚切换视频的一瞬间页面上没有标记，所以稍等再试两次。"""
        for attempt in range(3):
            pages = self._pages()
            if link := next((u for u in (video.current(p) for p in reversed(pages)) if u), None):
                return link
            if attempt < 2:
                time.sleep(0.7)
        return None

    def _video_dl_page(self):
        """短视频下载用的无头页面，从不动用户的窗口。
        用户开着窗口时同一个 profile 不能再开，就另起一个临时浏览器，把窗口里的 cookie 复制过去。"""
        self._check_alive()
        if not self._ctx or self._headless:
            self._close_dl()
            return self._page(headless=True)
        if self._dl:
            try:
                self._dl[1].cookies()
            except Exception:  # noqa: BLE001  后台浏览器意外关了，重开
                self._close_dl()
        if not self._dl:
            b, ctx = browsers.launch_temp(self._pw, browsers.pick(self._browser_key()), direct=self._direct)
            self._dl = (b, ctx, ctx.new_page())
        self._dl[1].add_cookies(self._ctx.cookies())  # 窗口里登录了就带上
        return self._dl[2]

    def _transfer_reporter(self) -> Callable[[int, int], None]:
        return transfer_reporter(self.emit)

    def cmd_download(self, kind: str, book_id: str, out_root: str, delay: float, **opts) -> None:
        """kind：novel 小说（opts: include_paid）；drama/comic 短剧漫剧（opts: start, end, save_mode）。"""
        if not book_id:
            urls = [p.url for p in self._pages()]
            url = next((u for u in reversed(urls) if fanqie.book_id_from_url(u)), "")
            if not url:
                raise RuntimeError("没有 BookID：请填写，或者先在浏览器里打开一个详情页")
            book_id, page_kind = fanqie.book_id_from_url(url), fanqie.kind_from_url(url)
            self.emit("grab", {"id": book_id, "kind": page_kind})  # 界面会跟着切类型
            if page_kind and page_kind != kind:
                name = fanqie.KIND_NAMES[page_kind]
                raise RuntimeError(f"浏览器当前打开的是{name}，类型已切换为{name}，确认设置后再点下载")
        page = self._page(headless=self._headless if self._ctx else True)
        if not fanqie.is_logged_in(page):
            self.emit("login", {"ok": False})
            raise RuntimeError("番茄达人中心还没登录，请先点右上角「去登录」")
        common = dict(
            delay=delay,
            log=lambda text: self.emit("log", {"text": text}),
            progress=lambda i, n: self.emit("progress", {"i": i, "n": n}),
            should_stop=self._stop.is_set,
        )
        if kind == "novel":
            result = fanqie.download_novel(page, book_id, Path(out_root), include_paid=opts["include_paid"], **common)
        else:
            from caifeng.media import concat_mp4

            result = fanqie.download_video(
                page,
                book_id,
                kind,
                Path(out_root),
                start=opts.get("start"),
                end=opts.get("end"),
                save_mode=opts.get("save_mode", "split"),
                merge=concat_mp4,
                transfer=self._transfer_reporter(),
                **common,
            )
        self.emit("done", result)

    # ---- 命令：短视频 ----
    def cmd_new_guest(self, domain: str) -> None:
        """换个游客身份：清掉这个网站的 cookie，窗口和后台解析用的浏览器都清（小红书游客看多了要登录）。"""
        if not self._ctx:
            self._page(headless=True)
        pattern = re.compile(re.escape(domain) + "$")
        for ctx in (self._ctx, self._dl[1] if self._dl else None):
            if ctx is not None:
                ctx.clear_cookies(domain=pattern)

    def cmd_resolve(
        self,
        link: str,
        on_done: Callable[[list], None],
        on_fail: Callable[[str], None],
        on_start: Callable[[], bool] | None = None,
        nomark: bool = False,
    ) -> None:
        """打开链接拿到里面的作品，结果交给回调。on_start 返回 False 就不解析了（解析对话框点了取消）。"""
        wait = self._last_resolve + RESOLVE_GAP - time.monotonic()
        if wait > 0:  # 一次贴了好几个链接：一个一个隔开打开，别连续请求平台
            time.sleep(wait)
        self._last_resolve = time.monotonic()
        if on_start and not on_start():
            return
        page = None
        try:
            page = self._video_dl_page()
            items = video.resolve(page, link, nomark)
        except Exception as e:  # noqa: BLE001
            on_fail(str(e) or e.__class__.__name__)
            return
        finally:
            if page is not None:
                with contextlib.suppress(Exception):
                    page.goto("about:blank")  # 作品页会自动播放出声，用完就清空
        on_done(items)
