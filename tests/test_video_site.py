from pathlib import Path

from caifeng.sites import bilibili, video
from caifeng.sites.common import Item


def test_links_in_share_text_and_mixed_platforms():
    text = "6.46 复制打开抖音，看看【黑白的作品】铁索连环 https://v.douyin.com/skOH7emgqrc/ m@d.Ag 01/02 TYM:/"
    assert video.links_in(text) == ["https://v.douyin.com/skOH7emgqrc/"]
    mixed = (
        "https://v.douyin.com/aaa/ 还有【准 备 挨 电⚡-哔哩哔哩】 https://b23.tv/BV12hp46jEo9，"
        "以及 https://www.bilibili.com/video/BV12hp46jEo9/?p=2 和 https://v.douyin.com/aaa/"
    )
    assert video.links_in(mixed) == [
        "https://v.douyin.com/aaa/",
        "https://b23.tv/BV12hp46jEo9",
        "https://www.bilibili.com/video/BV12hp46jEo9/?p=2",
    ]
    assert video.links_in("https://example.com/x") == []
    assert video.links_in("") == []


def test_platform_of():
    assert video.platform_of("https://www.douyin.com/video/1").NAME == "抖音"
    assert video.platform_of("https://b23.tv/abc").NAME == "哔哩哔哩"
    assert video.platform_of("https://example.com") is None


def test_file_stem():
    item = Item(platform="抖音", id="1", title="a/b?c", url="", created=1791433161)
    assert video.file_stem(item).endswith(" a_b_c")
    assert video.file_stem(Item(platform="抖音", id="42", title="", url="")).endswith(" 42")


def test_bilibili_pick_dash_prefers_quality_then_h264():
    def v(qn, h, codecid, bw):
        return {"id": qn, "height": h, "codecid": codecid, "bandwidth": bw, "baseUrl": f"{qn}-{codecid}"}

    dash = {
        "video": [v(32, 480, 12, 700), v(80, 1080, 13, 900), v(80, 1080, 7, 2000), v(80, 1080, 12, 1500)],
        "audio": [{"bandwidth": 65000, "baseUrl": "low"}, {"bandwidth": 177000, "baseUrl": "high"}],
    }
    best, audio = bilibili.pick_dash(dash)
    assert best["baseUrl"] == "80-7" and audio["baseUrl"] == "high"


def test_bilibili_official_nodes_first():
    urls = [
        "https://x.mcdn.bilivideo.cn:8082/v1/a",
        "https://upos-sz-mirrorcos.bilivideo.com/a",
        "",
        "https://e.edge.mountaintoys.cn/a",
    ]
    assert bilibili.by_reliability(urls)[0].startswith("https://upos-sz-mirrorcos")
    assert len(bilibili.by_reliability(urls)) == 3


def test_free_name_numbers_like_a_browser(tmp_path: Path):
    f = tmp_path / "a.mp4"
    assert video.free_name(f) == f
    f.write_bytes(b"x")
    assert video.free_name(f).name == "a (1).mp4"
    (tmp_path / "a (1).mp4").write_bytes(b"x")
    assert video.free_name(f).name == "a (2).mp4"
    (tmp_path / "图文").mkdir()
    assert video.free_name(tmp_path / "图文").name == "图文 (1)"


def test_kuaishou_links_and_best_url():
    from caifeng.sites import kuaishou

    assert video.platform_of("https://v.kuaishou.com/abcd").NAME == "快手"
    assert kuaishou.photo_id_from_url("https://www.kuaishou.com/short-video/3xftja2x753rdzc?x=1") == "3xftja2x753rdzc"
    assert kuaishou.photo_id_from_url("https://v.m.chenzhongtech.com/fw/photo/3xftja2x753rdzc") == "3xftja2x753rdzc"
    assert kuaishou.photo_id_from_url("https://v.kuaishou.com/abcd") is None
    photo = {
        "photoUrl": "p",
        "manifest": {"adaptationSet": [{"representation": [
            {"url": "720", "width": 720, "height": 1280, "avgBitrate": 1400, "backupUrl": ["720b"]},
            {"url": "1080", "width": 1080, "height": 1920, "avgBitrate": 2600},
        ]}]},
    }  # fmt: skip
    assert kuaishou.best_url(photo) == ("1080", ["p"], "1080P")
    assert kuaishou.best_url({"photoUrl": "p"}) == ("p", [], "")


def test_xiaohongshu_links_streams_and_images():
    from caifeng.sites import xiaohongshu as xhs

    assert video.platform_of("http://xhslink.com/a/AbCd").NAME == "小红书"
    nid = "6a954886000000002800515d"
    assert xhs.note_id_from_url(f"https://www.xiaohongshu.com/explore/{nid}?xsec_token=x") == nid
    assert xhs.note_id_from_url(f"https://www.xiaohongshu.com/discovery/item/{nid}?a=1") == nid
    streams = {
        "h265": [{"masterUrl": "265", "width": 1080, "height": 1920}],
        "h264": [{"masterUrl": "264-720", "width": 720, "height": 1280, "backupUrls": ["b"]},
                 {"masterUrl": "264-1080", "width": 1080, "height": 1920}],
    }  # fmt: skip
    assert xhs.best_stream(streams) == ("264-1080", [], "1080P")
    assert xhs.best_stream({"h265": streams["h265"]})[0] == "265"
    url = "http://sns-webpic-qc.xhscdn.com/2026/abc/1040g0083250!nd_dft_wlteh_webp_3"
    assert xhs.image_url({"url": url, "file": ""}) == "https://ci.xiaohongshu.com/1040g0083250?imageView2/format/jpg"


def test_shipinhao_is_recognized_but_not_supported():
    import pytest

    from caifeng.sites import shipinhao

    assert video.links_in("看看 https://weixin.qq.com/sph/Azqaj2gCc0") == ["https://weixin.qq.com/sph/Azqaj2gCc0"]
    with pytest.raises(RuntimeError, match="只能在微信里"):
        shipinhao.fetch(None, "https://weixin.qq.com/sph/Azqaj2gCc0")


def test_save_image_post_writes_text(tmp_path: Path, monkeypatch):
    def fake_fetch(url, dst, should_stop=None, on_bytes=None, headers=None, direct=False):
        dst.write_bytes(b"jpg")
        return True

    monkeypatch.setattr(video, "fetch_file", fake_fetch)
    item = Item(platform="小红书", id="1", title="春天", url="", images=["u1", "u2"], text="标题\n\n正文")
    r = video.save(item, tmp_path, log=lambda s: None)
    folder = Path(r["file"])
    assert sorted(f.name for f in folder.iterdir()) == ["01.jpg", "02.jpg", "文案.txt"]
    assert (folder / "文案.txt").read_text(encoding="utf-8") == "标题\n\n正文\n"


def test_save_image_post_stopped_leaves_no_folder(tmp_path: Path, monkeypatch):
    calls = []

    def fake_fetch(url, dst, should_stop=None, on_bytes=None, headers=None, direct=False):
        calls.append(url)
        dst.write_bytes(b"jpg")
        return len(calls) < 2  # 第二张时点了停止

    monkeypatch.setattr(video, "fetch_file", fake_fetch)
    item = Item(platform="小红书", id="1", title="春天", url="", images=["u1", "u2", "u3"])
    assert video.save(item, tmp_path, log=lambda s: None)["stopped"] is True
    assert list((tmp_path / "小红书").iterdir()) == []


def test_doubao_bad_video_model_is_ignored():
    from caifeng.sites import doubao

    assert doubao.video_urls({"download_url": "https://a/v.mp4", "video_model": "{坏的"}) == ["https://a/v.mp4"]


def test_names_safe_on_windows(tmp_path: Path):
    from caifeng.sites.common import safe_name

    assert safe_name("绝了...") == "绝了"
    assert safe_name("a\tb\x01c ") == "a_b_c"
    item = Item(platform="小红书", id="1", title="x" * 39 + ". 后面", url="", created=1791433161)
    assert not video.file_stem(item).endswith(".")
    item.title = "x" * 38 + " . 后面"  # 截断后末尾是「空格+点」
    assert not video.file_stem(item).endswith((".", " "))
    folder = tmp_path / "2026 v1.2 教程"
    folder.mkdir()
    assert video.free_name(folder).name == "2026 v1.2 教程 (1)"


def test_fetch_file_rejects_cut_off_download(tmp_path: Path):
    import http.server
    import threading

    import pytest

    from caifeng.sites.common import fetch_file

    class Short(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"x" * 100)  # 说好 1000，只给 100 就断开

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Short)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    dst = tmp_path / "v.mp4"
    with pytest.raises(OSError, match="不完整"):
        fetch_file(f"http://127.0.0.1:{srv.server_port}/", dst, direct=True)
    srv.server_close()
    assert list(tmp_path.iterdir()) == []  # 没有半截文件


def test_doubao_share_data_old_and_new_nesting():
    import json

    from caifeng.sites import doubao

    data = {"share_info": {"share_name": "对话"}, "message_snapshot": {"message_list": []}}
    old = json.dumps([{"data": data}])
    inner = json.dumps({"isMobileShareId": True, "data": data})
    new = json.dumps(["thread_(token)/page", [{"key": "shareInfo", "routerDataFnArgs": [inner]}]])
    assert doubao.share_data([old]) == data
    assert doubao.share_data(['["thread_layout",[]]', new]) == data
    assert doubao.share_data(["不是 JSON", '["thread_layout",[]]']) == {}


def test_doubao_aes_matches_fips_197():
    from caifeng.sites import doubao

    ct = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")  # FIPS-197 附录 C.1
    assert doubao.aes_cbc_decrypt(ct, bytes(range(16)), bytes(16)).hex() == "00112233445566778899aabbccddeeff"


def test_doubao_decode_url_plain_base64_and_qaab():
    import base64

    from caifeng.sites import doubao

    assert doubao.decode_url("https://a/v.mp4") == "https://a/v.mp4"
    assert doubao.decode_url(base64.b64encode(b"https://a/v.mp4").decode()) == "https://a/v.mp4"
    # 2026-10 从豆包抓到的一组真实数据，结果和 ihmily/doubao-nomark 的实现一致
    token = "qAABAMBC+hGRANCoMwmqUhDJ1xvh574PHogoeFoqXWr7d8BTHbL4DJcelJ5Ma4hr+kKyE0wJQfCNfPSRjAjfGRpmxkbfGZeZaz3rh0TRbY5F4cYebAOfhzc8+EYU1Kokv6EkT0A/vykSkhRKdeVV42BTBhxcuGSM1Nbof2aVvd0FSj1IyViOLHbxV86z2rk3jiwUPRvxSOJp92E7sgl21cEhq8LkFMu5jZujLdFOvG+Z4Y9t5dQnwKZUrr51jc4b3RV50rwCa42LMHXwWqDXtH6Wa8clcqaBWiJtZkQ1kOMhoggMfKs1uM14Gdqocn12OAFfP5iHL7Re2Xe7TUXh7dGQ+iVc3Or7wr3QDHwE1FKD4PENCmoVXSKzevp/Qf3fZhuyT2SwCfYb5PqR3khnOOIscCHEJU4Clw/n8NdQ7J92YkTzlDXnYATbPx5exKp0IrYlHTE8CJoL0cwpmAEEQnR74gymml5P/rgbj/djK5C7vjkLf0sjR1qb/tylehNwd08ZqQvDdx+o4G/yjRfAGaDNjWoMBm4BLAl1AXO9XrTC+6ek9sx9pGdLOkemKTLxfpLsBhCMnjiyu7YsOZ9isRI/9Gd8nTdHCgVU8KvdUMYsWxKsTwLUVjqfnyNOzwkSGbd1sg=="  # noqa: E501
    key_seed = "Ey8EST9NiQfUgeY7VmI/3hDbDummJpDehqWAMJBkPRY="
    assert (
        doubao.decode_url(token, key_seed)
        == "https://v3-vdl.doubao.com/45ea4d68df790fb7131e2a2c70b33731/6ace5fa3/video/tos/cn/tos-cn-v-9ecd54/780a86b89048468badf88ca038d90356/?a=482431&ch=0&cr=4&dr=0&er=0&lr=unwatermarked&net=5&cd=0%7C1%7C0%7C4&cv=1&br=31782&bt=31782&cs=0&ds=3&ft=4FG0-X_nY28Qjju9wUxyc~3BHC~PRIVAhIniXwG&mime_type=video_mp4&qs=13&rc=anc3ajpvOmVtZTczNGY5M0Bpanc3ajpvOmVtZTczNGY5M0BfL2lkYWFeYDFhLS1kNi9zYSNfL2lkYWFeYDFhLS1kNi9zcw%3D%3D&btag=c0000e00008000&dy_q=1791906191&l=2026101323431187D67BFADC6387625C3C"
    )
    assert doubao.decode_url(token, "") == ""
    assert doubao.decode_url("乱七八糟", key_seed) == ""


def test_doubao_clean_api_only_rewrites_bytedance_video_api():
    from caifeng.sites import doubao

    api = "https://vas-lf-x.snssdk.com/video/fplay/1/abc/?vid=v1&logo_type=video_gen_watermark_dyn&codec_type=5"
    clean = doubao.clean_api(api)
    assert "logo_type=unwatermarked" in clean and "codec_type=8" in clean and "vid=v1" in clean
    assert "watermark_dyn" not in clean
    assert doubao.clean_api("https://evil.example.com/video/fplay/1/?vid=v1") == ""
    assert doubao.clean_api("http://vas-lf-x.snssdk.com/video/fplay/1/") == ""
    assert doubao.clean_api("") == ""


def test_doubao_fetch_only_tries_clean_url_when_asked(monkeypatch):
    import json

    from caifeng.sites import doubao

    video = {"vid": "v1", "download_url": "https://a/labeled.mp4", "video_model": "{}"}
    block = {"content": {"creation_block": {"creations": [{"video": video}]}}}
    message = {"content": json.dumps([block]), "create_time": 1}
    data = {"share_info": {"share_name": "对话"}, "message_snapshot": {"message_list": [message]}}

    class Page:
        def goto(self, *a, **kw):
            pass

        def wait_for_function(self, *a, **kw):
            pass

        def evaluate(self, js):
            return [json.dumps([{"data": data}])]

    asked = []
    monkeypatch.setattr(doubao, "clean_video", lambda v: asked.append(v["vid"]) or "")
    (item,) = doubao.fetch(Page(), "https://www.doubao.com/thread/a")
    assert asked == [] and item.video == "https://a/labeled.mp4" and item.note == ""
    (item,) = doubao.fetch(Page(), "https://www.doubao.com/thread/a", nomark=True)
    assert asked == ["v1"] and item.note.startswith("带 AI 标识")  # 换不到就下带标识的，并注明
    assert item.tags == {} and item.tags_from == ""  # 下的是带标识的版本，不用补
    monkeypatch.setattr(doubao, "clean_video", lambda v: "https://a/clean.mp4")
    (item,) = doubao.fetch(Page(), "https://www.doubao.com/thread/a", nomark=True)
    assert item.video == "https://a/clean.mp4" and item.video_alt == ["https://a/labeled.mp4"]
    assert '"Label": "1"' in item.tags["AIGC"] and item.tags_from == "https://a/labeled.mp4"  # 下完补回隐式标识


def test_save_writes_tags_read_from_labeled_copy(tmp_path, monkeypatch):
    from caifeng import media
    from caifeng.sites import video

    def fake_fetch(urls, dst, *a, **kw):
        dst.write_bytes(b"x")
        return True

    written = {}
    monkeypatch.setattr(video, "_fetch_any", fake_fetch)
    monkeypatch.setattr(
        media, "read_tags", lambda url, keys, **kw: {"AIGC": "平台原值"} if url == "https://a/l.mp4" else {}
    )
    monkeypatch.setattr(media, "write_tags", lambda path, tags: written.update(tags))
    item = Item(
        platform="豆包",
        id="v1",
        title="对话",
        url="",
        video="https://a/c.mp4",
        tags={"AIGC": "默认"},
        tags_from="https://a/l.mp4",
    )
    video.save(item, tmp_path, log=lambda t: None)
    assert written == {"AIGC": "平台原值"}  # 读得到平台写的原值就用它
    monkeypatch.setattr(media, "read_tags", lambda *a, **kw: (_ for _ in ()).throw(OSError("连不上")))
    video.save(item, tmp_path, log=lambda t: None)
    assert written == {"AIGC": "默认"}  # 读不到就用默认值，照样写
