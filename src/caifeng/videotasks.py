"""短视频的下载列表：解析交给 SiteWorker 的浏览器线程，下载在这里自己的线程里按顺序做，所以下载时贴新链接也能马上解析。

列表存盘，重开后没下完的变成「已停止」，重试会重新解析（下载地址有时效）。
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from caifeng.sites import video, xiaohongshu
from caifeng.sites.common import Item

Emit = Callable[[str, dict[str, Any]], None]
Resolve = Callable[[str, str], None]  # (任务 id, 链接)：交给浏览器线程去解析，结果回调 resolved / failed

PARSING, WAITING, DOWNLOADING, MERGING, DONE, FAILED, STOPPED = (
    "parsing", "waiting", "downloading", "merging", "done", "failed", "stopped",
)  # fmt: skip
ACTIVE = (PARSING, WAITING, DOWNLOADING, MERGING)


@dataclass
class Task:
    id: str
    link: str
    status: str = PARSING
    platform: str = ""
    title: str = ""
    author: str = ""
    cover: str = ""
    quality: str = ""
    note: str = ""  # 比如「没登录 B 站，最高 480P」
    error: str = ""
    file: str = ""  # 下好的视频或图文文件夹
    size: int = 0  # 下好的大小
    item_id: str = ""  # 平台上的作品 ID（豆包一个链接多个视频时，重试靠它找回是哪一个）
    picks: list[int] | None = None  # 图文只下了其中几张（下标），重试时照旧
    added: float = 0
    # 下载中的进度，不存盘
    got: int = 0
    total: int = 0
    speed: float = 0


TRANSIENT = ("got", "total", "speed")


GAP = 3.0  # 两个下载之间至少隔几秒：批量下载时别连续请求平台


class TaskList:
    def __init__(self, emit: Emit, resolve: Resolve, out_root: Callable[[], Path], store: Path, gap: float = GAP):
        self.emit = emit
        self._resolve = resolve
        self._out_root = out_root
        self._store = store
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._items: dict[str, Item] = {}  # 解析出来、还没下完的作品（带下载地址，不存盘）
        self._cancel: set[str] = set()
        self._wake = threading.Event()
        self._quit = False
        self._gap = gap
        self._next_at = 0.0  # 下一个下载最早什么时候开始（time.monotonic）
        self._load()
        self._thread = threading.Thread(target=self._run, daemon=True, name="video-download")
        self._thread.start()

    def all(self) -> list[dict]:
        with self._lock:
            return [self._public(t) for t in self._tasks.values()]

    def add_items(self, rows: list[tuple[str, Item, list[int] | None]]) -> None:
        """已经解析好、用户选中的作品：直接排队下载。rows 是 (原链接, 作品, 图文选了哪几张)，重试时用原链接重新解析。"""
        for link, item, picks in rows:
            t = Task(id=uuid.uuid4().hex[:12], link=link, added=time.time(), status=WAITING, picks=picks)
            item = _picked(item, picks)
            _fill(t, item)
            with self._lock:
                self._tasks[t.id] = t
                self._items[t.id] = item
            self._changed(t, save=True)
        self._wake.set()

    def stop(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status not in ACTIVE:
                return
            if t.status in (DOWNLOADING, MERGING):
                self._cancel.add(task_id)  # 下载线程看到后停下，改成已停止
                return
            t.status = STOPPED
        self._changed(t, save=True)

    def retry(self, task_id: str) -> None:
        """重新解析再下：下载地址有时效，重开软件后旧地址可能已经不能用。"""
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status in ACTIVE:
                return
            t.status, t.error = PARSING, ""
            self._items.pop(task_id, None)
        self._changed(t, save=True)
        self._resolve(t.id, t.link)

    def remove(self, task_id: str) -> None:
        with self._lock:
            t = self._tasks.pop(task_id, None)
            self._items.pop(task_id, None)
            if t and t.status in (DOWNLOADING, MERGING):
                self._cancel.add(task_id)
        if t:
            self.emit("task_remove", {"id": task_id})
            self._save()

    def clear_done(self) -> None:
        with self._lock:
            gone = [i for i, t in self._tasks.items() if t.status == DONE]
            for i in gone:
                del self._tasks[i]
        for i in gone:
            self.emit("task_remove", {"id": i})
        self._save()

    def close(self) -> None:
        self._quit = True
        with self._lock:
            self._cancel.update(i for i, t in self._tasks.items() if t.status in (DOWNLOADING, MERGING))
        self._wake.set()
        self._thread.join(timeout=5)

    # ---- 浏览器线程解析完回调 ----
    def resolved(self, task_id: str, items: list[Item]) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status != PARSING:
                return  # 解析期间被删掉或停止了
            if not items:
                t.status, t.error = FAILED, "没有解析出视频"
            if t.item_id:  # 重试：一个链接多个作品时找回原来那一个
                items = [i for i in items if i.id == t.item_id] or items[:1]
            items = [_picked(i, t.picks) for i in items[:1]] + items[1:]
            new = [t] + [Task(id=uuid.uuid4().hex[:12], link=t.link, added=t.added) for _ in items[1:]]
            for task, item in zip(new, items, strict=False):
                _fill(task, item)
                task.status = WAITING
                self._tasks[task.id] = task
                self._items[task.id] = item
        for task in new[: max(len(items), 1)]:
            self._changed(task, save=True)
        self._wake.set()

    def failed(self, task_id: str, error: str) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t or t.status != PARSING:
                return
            t.status, t.error = FAILED, error
        self._changed(t, save=True)

    # ---- 下载线程：按加入顺序一个一个下 ----
    def _next(self) -> Task | None:
        with self._lock:
            waiting = [t for t in self._tasks.values() if t.status == WAITING and t.id in self._items]
            return min(waiting, key=lambda t: t.added, default=None)

    def _run(self) -> None:
        while not self._quit:
            t = self._next()
            wait = self._next_at - time.monotonic()
            if t is None or wait > 0:
                self._wake.wait(1 if t is None else min(wait, 1))
                self._wake.clear()
                continue
            with contextlib.suppress(Exception):  # 哪条出了意外也不能让下载线程停掉
                self._download(t)
            self._next_at = time.monotonic() + self._gap

    def _download(self, t: Task) -> None:
        with self._lock:
            # 选中之后、开始之前可能被删掉或停止了
            item = self._items.get(t.id)
            if item is None or self._tasks.get(t.id) is not t or t.status != WAITING:
                return
            t.status, t.got, t.total, t.speed = DOWNLOADING, 0, 0, 0
            self._cancel.discard(t.id)
        self._changed(t)
        try:
            result = video.save(
                item,
                self._out_root(),
                log=lambda text: self._on_log(t, text),
                transfer=self._reporter(t),
                should_stop=lambda: t.id in self._cancel,
            )
        except Exception as e:  # noqa: BLE001  一条失败，接着下下一条
            with self._lock:
                t.status, t.error = FAILED, str(e) or e.__class__.__name__
        else:
            with self._lock:
                if result.get("stopped"):
                    t.status = STOPPED
                else:
                    t.status, t.file = DONE, result.get("file", "")
                    with contextlib.suppress(OSError):
                        p = Path(t.file)
                        t.size = p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.iterdir())
                    self._items.pop(t.id, None)
        with self._lock:
            self._cancel.discard(t.id)
            gone = t.id not in self._tasks  # 下载中被删掉了
        if not gone:
            self._changed(t, save=True)

    def _on_log(self, t: Task, text: str) -> None:
        if text.startswith("合并"):
            t.status = MERGING
            self._changed(t)

    def _reporter(self, t: Task) -> Callable[[int, int], None]:
        """每收到一块数据回调一次；节流成每 0.3 秒一个事件，速度取最近约 2 秒的平均。"""
        samples: list[tuple[float, int]] = []
        state = {"done": 0, "last": 0, "emit": 0.0}

        def report(got: int, size: int) -> None:
            now = time.monotonic()
            if got < state["last"]:  # 换了一个文件（B 站画面、声音；图文的下一张）
                state["done"] += state["last"]
            state["last"] = got
            samples.append((now, state["done"] + got))
            while len(samples) > 2 and now - samples[0][0] > 2:
                samples.pop(0)
            if now - state["emit"] < 0.3 and got != size:
                return
            state["emit"] = now
            span = now - samples[0][0]
            t.got, t.total = got, size
            t.speed = (samples[-1][1] - samples[0][1]) / span if span > 0 else 0
            self._changed(t)

        return report

    def _public(self, t: Task) -> dict:
        d = dataclasses.asdict(t)
        # 小红书要求登录：界面上给「登录」和「换个游客身份」两个按钮
        d["wall"] = xiaohongshu.NAME if t.status == FAILED and t.error.startswith(xiaohongshu.LOGIN_WALL) else ""
        return d

    def _changed(self, t: Task, save: bool = False) -> None:
        with self._lock:
            if self._tasks.get(t.id) is not t:  # 已经删掉了，别让界面又把它加回来
                return
        self.emit("task", self._public(t))
        if save:
            self._save()

    def _save(self) -> None:
        with self._lock:  # 界面、浏览器线程、下载线程都会存，一次只让一个写
            rows = [dataclasses.asdict(t) for t in self._tasks.values()]
            rows = [{k: v for k, v in r.items() if k not in TRANSIENT} for r in rows]
            with contextlib.suppress(OSError):
                self._store.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._store.with_suffix(".tmp")
                tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self._store)

    def _load(self) -> None:
        try:
            rows = json.loads(self._store.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return
        names = {f.name for f in dataclasses.fields(Task)}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            t = Task(**{k: v for k, v in row.items() if k in names})
            if t.status in ACTIVE:  # 上次没下完：下载地址可能过期了，要重试
                t.status = STOPPED
            self._tasks[t.id] = t


def _picked(item: Item, picks: list[int] | None) -> Item:
    """图文只留选中的几张。"""
    if picks is None or not item.images:
        return item
    keep = [i for i in picks if 0 <= i < len(item.images)]
    thumbs = [item.thumbs[i] for i in keep if i < len(item.thumbs)]
    return dataclasses.replace(item, images=[item.images[i] for i in keep], thumbs=thumbs)


def _fill(t: Task, item: Item) -> None:
    t.platform, t.title, t.author, t.cover = item.platform, item.title, item.author, item.cover
    t.quality, t.note, t.item_id, t.error = item.quality, item.note, item.id, ""
    if item.images:
        t.quality = f"图文 {len(item.images)} 张"
