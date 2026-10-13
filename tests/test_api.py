from pathlib import Path

import pytest

from caifeng.api import Api
from caifeng.config import ConfigStore


@pytest.fixture
def api(tmp_path: Path) -> Api:
    return Api(ConfigStore(tmp_path / "config.json"), downloads=tmp_path / "Downloads")


def test_state_has_defaults_and_paths(api: Api, tmp_path: Path):
    st = api.get_state()
    assert st["settings"]["browser"] == ""  # 空 = 本机检测到的第一个
    assert all(set(b) == {"key", "name"} for b in st["browsers"])
    assert st["browser"] in ("", *[b["key"] for b in st["browsers"]])
    root = tmp_path / "Downloads" / "采风"
    assert st["paths"]["output_root"] == str(root)
    assert st["paths"]["video"] == str(root / "视频")
    assert st["paths"]["fanqie"] == str(root / "番茄")


def test_partial_patch_is_merged_and_persisted(api: Api, tmp_path: Path):
    st = api.save_settings({"appearance": "dark", "fanqie": {"save_mode": "both"}})
    assert st["settings"]["appearance"] == "dark"
    assert st["settings"]["fanqie"]["save_mode"] == "both"
    assert st["settings"]["fanqie"]["kind"] == "novel"  # 没传的字段不变
    reloaded = ConfigStore(tmp_path / "config.json").load()
    assert reloaded.appearance == "dark" and reloaded.fanqie.save_mode == "both"


def test_bad_values_are_ignored(api: Api):
    st = api.save_settings({"fanqie": {"delay": "fast"}, "nonsense": 1})
    assert st["settings"]["fanqie"]["delay"] == 1.0
    assert "nonsense" not in st["settings"]


def test_tool_dir_equal_to_default_is_not_stored(api: Api, tmp_path: Path):
    default = str(tmp_path / "Downloads" / "采风" / "视频")
    st = api.save_settings({"video": {"output_dir": default}})
    assert st["settings"]["video"]["output_dir"] == ""
    custom = str(tmp_path / "elsewhere")
    st = api.save_settings({"video": {"output_dir": custom}})
    assert st["settings"]["video"]["output_dir"] == custom
    assert st["paths"]["video"] == custom


def test_tool_dir_follows_new_output_root(api: Api, tmp_path: Path):
    st = api.save_settings({"output_dir": str(tmp_path / "root")})
    assert st["paths"]["fanqie"] == str(tmp_path / "root" / "番茄")


def test_pick_folder_without_window_returns_none(api: Api):
    assert api.pick_folder() is None


def test_check_login_without_profile_reports_logged_out_without_browser(api: Api, tmp_path: Path):
    events = []
    api._emit = lambda site, kind, data: events.append((site, kind, data))
    api.save_settings({"data_dir": str(tmp_path / "data")})
    api.check_login("fanqie")
    assert events == [("fanqie", "login", {"ok": False})]
    assert api._workers == {}  # 没有启动后台浏览器


class FakeVideoWorker:
    """代替浏览器线程：按链接直接回调解析结果。"""

    def __init__(self, results):
        self.results = results
        self.nomark = []

    def post(self, cmd, link, on_done, on_fail, on_start=None, nomark=False):
        assert cmd == "resolve"
        self.nomark.append(nomark)
        if on_start and not on_start():
            return
        r = self.results[link]
        on_fail(r) if isinstance(r, str) else on_done(r)


def test_video_parse_then_download_chosen(api: Api, monkeypatch):
    from caifeng.sites.common import Item

    events, added = [], []
    monkeypatch.setattr(api, "_emit", lambda site, kind, data: events.append((kind, data)))
    items = [Item(platform="豆包", id=f"v{i}", title=f"对话 {i}", url="") for i in (1, 2)]
    worker = FakeVideoWorker({"https://www.doubao.com/thread/a": items, "https://v.douyin.com/bad/": "作品已删除"})
    monkeypatch.setattr(api, "_worker", lambda site: worker)
    fake_list = type("L", (), {"add_items": lambda self, pairs: added.extend(pairs)})()
    monkeypatch.setattr(api, "_video_list", lambda: fake_list)

    r = api.video_add("看 https://www.doubao.com/thread/a 还有 https://v.douyin.com/bad/")
    assert r == {"ok": True, "n": 2, "message": ""}
    kinds = [k for k, _ in events]
    assert kinds == ["parse_start", "parse_step", "parse_item", "parse_item", "parse_step", "parse_error", "parse_done"]
    keys = [d["key"] for k, d in events if k == "parse_item"]
    api.video_download([keys[1]])
    assert [(link, it.title, picks) for link, it, picks in added] == [
        ("https://www.doubao.com/thread/a", "对话 2", None)
    ]

    assert api.video_add("没有链接")["ok"] is False


def test_video_parse_cancel_skips_the_rest(api: Api, monkeypatch):
    events = []
    monkeypatch.setattr(api, "_emit", lambda site, kind, data: events.append(kind))

    class Later:  # 先记下来，取消后再执行，模拟浏览器线程还在排队
        queued = []

        def post(self, cmd, **kw):
            self.queued.append(kw)

    w = Later()
    monkeypatch.setattr(api, "_worker", lambda site: w)
    api.video_add("https://v.douyin.com/a/ https://v.douyin.com/b/")
    api.video_parse_cancel()
    for kw in w.queued:
        assert kw["on_start"]() is False  # 取消后剩下的链接不再解析
    assert events == ["parse_start"]


def test_video_download_only_picked_images(api: Api, monkeypatch):
    from caifeng.sites.common import Item

    added = []
    note = Item(platform="小红书", id="n1", title="图文", url="", images=["a", "b", "c"], text="正文")
    monkeypatch.setattr(api, "_emit", lambda *a: None)
    monkeypatch.setattr(api, "_worker", lambda site: FakeVideoWorker({"https://www.xiaohongshu.com/explore/x": [note]}))
    fake_list = type("L", (), {"add_items": lambda self, pairs: added.extend(pairs)})()
    monkeypatch.setattr(api, "_video_list", lambda: fake_list)
    api.video_add("https://www.xiaohongshu.com/explore/x")
    (key,) = api._parse["items"]
    api.video_download([key], {key: [1, 2]})
    assert added[0][2] == [1, 2] and note.images == ["a", "b", "c"]  # 原来的不动（选图在下载列表里应用）


def test_video_parse_empty_result_is_an_error(api: Api, monkeypatch):
    events = []
    monkeypatch.setattr(api, "_emit", lambda site, kind, data: events.append(kind))
    monkeypatch.setattr(api, "_worker", lambda site: FakeVideoWorker({"https://www.doubao.com/thread/a": []}))
    api.video_add("https://www.doubao.com/thread/a")
    assert events == ["parse_start", "parse_step", "parse_error", "parse_done"]


def test_xiaohongshu_login_wall_lets_user_switch_guest(api: Api, monkeypatch):
    from caifeng.sites import xiaohongshu
    from caifeng.sites.common import Item

    events, posted = [], []
    monkeypatch.setattr(api, "_emit", lambda site, kind, data: events.append((kind, data)))
    link = "https://www.xiaohongshu.com/explore/x"
    results = {link: f"{xiaohongshu.LOGIN_WALL}：去登录"}

    class Worker(FakeVideoWorker):
        def post(self, cmd, **kw):
            posted.append(cmd)
            if cmd == "new_guest":  # 换了身份，下次就能解析出来
                assert kw["domain"] == xiaohongshu.COOKIE_DOMAIN
                results[link] = [Item(platform="小红书", id="n", title="图文", url="", images=["a", "b"])]
                return
            super().post(cmd, **kw)

    monkeypatch.setattr(api, "_worker", lambda site: Worker(results))
    api.video_add(link)
    assert events[-2] == ("parse_error", {"link": link, "error": results[link], "wall": "小红书"})
    assert events[-1][0] == "parse_done"
    api.video_reparse(link, new_guest=True)
    assert posted == ["resolve", "new_guest", "resolve"]
    assert [k for k, _ in events[-3:]] == ["parse_step", "parse_item", "parse_done"]


def test_video_task_new_guest_clears_cookies_before_retry(api: Api, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api, "_worker", lambda site: type("W", (), {"post": lambda self, cmd, **kw: calls.append(cmd)})()
    )

    class FakeList:
        def stop(self, i):
            calls.append(("stop", i))

        def retry(self, i):
            calls.append(("retry", i))

        def remove(self, i):
            calls.append(("remove", i))

    fake_list = FakeList()
    monkeypatch.setattr(api, "_video_list", lambda: fake_list)
    api.video_task("new_guest", "t1")
    assert calls == ["new_guest", ("retry", "t1")]


def test_video_reparse_after_login_keeps_cookies(api: Api, monkeypatch):
    posted = []
    link = "https://www.xiaohongshu.com/explore/x"

    class Worker(FakeVideoWorker):
        def post(self, cmd, **kw):
            posted.append(cmd)
            super().post(cmd, **kw)

    monkeypatch.setattr(api, "_emit", lambda *a: None)
    monkeypatch.setattr(api, "_worker", lambda site: Worker({link: "小红书要求登录才能看：去登录"}))
    api.video_add(link)
    api.video_reparse(link)
    assert posted == ["resolve", "resolve"]


def test_video_add_passes_nomark_setting(api: Api, monkeypatch):
    worker = FakeVideoWorker({"https://www.doubao.com/thread/a": []})
    monkeypatch.setattr(api, "_worker", lambda site: worker)
    api.video_add("https://www.doubao.com/thread/a")
    api.save_settings({"video": {"nomark": True}})
    api.video_add("https://www.doubao.com/thread/a")
    assert worker.nomark == [False, True]  # 默认不去标识，打开设置后才去
