import threading
import time
from pathlib import Path

from caifeng import videotasks
from caifeng.sites.common import Item


def wait(cond, seconds=3):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def make(tmp_path: Path, monkeypatch, fail_title="", gate=None, gap=0):
    """不开浏览器、不联网：解析交给测试手动回调，下载换成写一个小文件。gate：下载前等它放行。"""
    saved = []
    starts = make.starts = []  # 每个下载开始的时间

    def fake_save(item, out_root, *, log, transfer, should_stop):
        if gate is not None:
            gate.wait(3)
        if item.title == fail_title:
            raise RuntimeError("链接失效")
        f = Path(out_root) / f"{item.title}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 10)
        transfer(10, 10)
        saved.append(item)
        starts.append(time.monotonic())
        return {"stopped": False, "file": str(f)}

    monkeypatch.setattr(videotasks.video, "save", fake_save)
    events, asked = [], []
    tl = videotasks.TaskList(
        emit=lambda kind, data: events.append((kind, dict(data))),
        resolve=lambda task_id, link: asked.append((task_id, link)),
        out_root=lambda: tmp_path / "out",
        store=tmp_path / "tasks.json",
        gap=gap,
    )
    return tl, events, asked, saved


def by_id(tl):
    return {t["id"]: t for t in tl.all()}


def test_items_download_in_order_and_clear_done(tmp_path, monkeypatch):
    tl, events, _, saved = make(tmp_path, monkeypatch)
    link = "https://www.doubao.com/thread/x"
    tl.add_items([(link, Item(platform="豆包", id=f"v{i}", title=f"对话 {i}", url=link), None) for i in (1, 2)])
    assert wait(lambda: [i.title for i in saved] == ["对话 1", "对话 2"])
    assert wait(lambda: all(t["status"] == "done" for t in tl.all()))
    assert tl.all()[0]["size"] == 10
    ids = list(by_id(tl))
    tl.clear_done()
    assert tl.all() == [] and ("task_remove", {"id": ids[0]}) in events
    tl.close()


def test_failed_download_retry_reparses(tmp_path, monkeypatch):
    tl, _, asked, _ = make(tmp_path, monkeypatch, fail_title="坏的")
    tl.add_items([("https://v.douyin.com/a/", Item(platform="抖音", id="2", title="坏的", url=""), None)])
    assert wait(lambda: tl.all()[0]["status"] == "failed")
    (t,) = tl.all()
    assert t["error"] == "链接失效"
    tl.retry(t["id"])  # 重试会重新解析
    assert asked == [(t["id"], "https://v.douyin.com/a/")] and tl.all()[0]["status"] == "parsing"
    tl.resolved(t["id"], [])  # 什么都没解析出来：不能一直卡在解析中
    assert tl.all()[0]["status"] == "failed" and tl.all()[0]["error"] == "没有解析出视频"
    tl.retry(t["id"])
    tl.failed(t["id"], "作品已删除")
    assert tl.all()[0]["error"] == "作品已删除"
    tl.remove(t["id"])
    assert tl.all() == []
    tl.close()


def test_remove_or_stop_while_queued(tmp_path, monkeypatch):
    gate = threading.Event()
    tl, _, _, saved = make(tmp_path, monkeypatch, gate=gate)
    tl.add_items([("l", Item(platform="抖音", id=str(i), title=str(i), url=""), None) for i in range(3)])
    assert wait(lambda: any(t["status"] == "downloading" for t in tl.all()))
    first, second, third = sorted(tl.all(), key=lambda t: t["added"])
    tl.remove(second["id"])
    tl.stop(third["id"])
    gate.set()
    assert wait(lambda: by_id(tl)[first["id"]]["status"] == "done")
    assert by_id(tl)[third["id"]]["status"] == "stopped" and [i.title for i in saved] == ["0"]
    tl.add_items([("l", Item(platform="抖音", id="9", title="9", url=""), None)])  # 下载线程还活着
    assert wait(lambda: [i.title for i in saved] == ["0", "9"])
    tl.close()


def test_retry_keeps_picked_images(tmp_path, monkeypatch):
    tl, _, asked, saved = make(tmp_path, monkeypatch)
    note = Item(platform="小红书", id="n", title="图文", url="", images=["a", "b", "c"])
    tl.add_items([("l", note, [0, 2])])
    assert wait(lambda: len(saved) == 1)
    assert saved[0].images == ["a", "c"] and tl.all()[0]["quality"] == "图文 2 张"
    tid = tl.all()[0]["id"]
    tl.retry(tid)
    tl.resolved(tid, [note])
    assert wait(lambda: len(saved) == 2) and saved[1].images == ["a", "c"]
    tl.close()


def test_list_is_kept_and_unfinished_become_stopped(tmp_path, monkeypatch):
    gate = threading.Event()
    tl, _, _, _ = make(tmp_path, monkeypatch, gate=gate)
    tl.add_items([("https://v.douyin.com/a/", Item(platform="抖音", id="1", title="a", url=""), [1])])
    tl._save()
    gate.set()
    tl.close()
    again, _, _, _ = make(tmp_path, monkeypatch)
    (t,) = again.all()
    assert t["link"] == "https://v.douyin.com/a/" and t["picks"] == [1]
    assert t["status"] in ("stopped", "done")
    again.close()


def test_login_wall_is_marked_for_the_ui(tmp_path, monkeypatch):
    from caifeng.sites import xiaohongshu

    tl, events, asked, _ = make(tmp_path, monkeypatch, fail_title="坏的")
    tl.add_items([("l", Item(platform="小红书", id="n", title="坏的", url=""), None)])
    assert wait(lambda: tl.all()[0]["status"] == "failed")
    assert tl.all()[0]["wall"] == ""
    tid = tl.all()[0]["id"]
    tl.retry(tid)
    tl.failed(tid, f"{xiaohongshu.LOGIN_WALL}：去登录")
    assert tl.all()[0]["wall"] == "小红书" and events[-1][1]["wall"] == "小红书"
    tl.close()


def test_removed_task_is_not_sent_again(tmp_path, monkeypatch):
    tl, events, _, _ = make(tmp_path, monkeypatch, gate=threading.Event())
    tl.add_items([("l", Item(platform="抖音", id="1", title="a", url=""), None)])
    (t,) = tl._tasks.values()
    tl.remove(t.id)
    n = len(events)
    tl._on_log(t, "合并画面和声音")  # 删掉之后下载线程还在报
    assert len(events) == n
    tl.close()


def test_downloads_are_spaced_by_gap(tmp_path: Path, monkeypatch):
    tl, *_ = make(tmp_path, monkeypatch, gap=0.6)
    try:
        tl.add_items(
            [
                ("https://x/1", Item("抖音", "1", "一", "https://x/1", video="v1"), None),
                ("https://x/2", Item("抖音", "2", "二", "https://x/2", video="v2"), None),
            ]
        )
        assert wait(lambda: len(make.starts) == 2, 5)
        assert make.starts[1] - make.starts[0] >= 0.55  # 批量下载之间强制隔开
    finally:
        tl.close()
