from pathlib import Path

import av
import pytest

from caifeng.media import concat_mp4, mux, write_tags


def make_clip(path: Path, seconds: int, shade: int) -> None:
    """生成一段带声音的小视频：每秒 10 帧纯色画面 + 8kHz 静音。"""
    with av.open(str(path), "w") as out:
        v = out.add_stream("mpeg4", rate=10)
        v.width, v.height, v.pix_fmt = 64, 48, "yuv420p"
        a = out.add_stream("aac", rate=8000)
        a.layout = "mono"
        for i in range(seconds * 10):
            frame = av.VideoFrame(64, 48, "yuv420p")
            for plane, value in zip(frame.planes, (shade, 128, 128), strict=True):
                plane.update(bytes([value]) * plane.buffer_size)
            frame.pts = i
            out.mux(v.encode(frame))
        samples = 1024
        for i in range(seconds * 8000 // samples):
            af = av.AudioFrame(format="fltp", layout="mono", samples=samples)
            af.planes[0].update(bytes(af.planes[0].buffer_size))
            af.sample_rate, af.pts = 8000, i * samples
            out.mux(a.encode(af))
        out.mux(v.encode())
        out.mux(a.encode())


def duration(path: Path) -> dict[str, float]:
    with av.open(str(path)) as c:
        return {s.type: float(s.duration * s.time_base) for s in c.streams}


def test_concat_mp4_joins_video_and_audio(tmp_path: Path):
    clips = [tmp_path / f"{i}.mp4" for i in range(3)]
    for i, c in enumerate(clips):
        make_clip(c, seconds=2 + i, shade=60 * i)
    dst = tmp_path / "all.mp4"
    assert concat_mp4(clips, dst)
    total = duration(dst)
    assert total["video"] == pytest.approx(9, abs=0.3)
    assert total["audio"] == pytest.approx(9, abs=0.3)
    with av.open(str(dst)) as c:
        frames = sum(1 for _ in c.decode(video=0))
    assert frames == 90
    assert not (tmp_path / "all.mp4.part").exists()


def test_concat_mp4_stop_leaves_nothing(tmp_path: Path):
    clips = [tmp_path / f"{i}.mp4" for i in range(2)]
    for c in clips:
        make_clip(c, seconds=1, shade=100)
    dst = tmp_path / "all.mp4"
    calls = []
    assert not concat_mp4(clips, dst, should_stop=lambda: calls.append(1) or len(calls) > 1)
    assert not dst.exists() and not (tmp_path / "all.mp4.part").exists()


def test_mux_joins_video_only_and_audio_only(tmp_path: Path):
    src = tmp_path / "src.mp4"
    make_clip(src, seconds=3, shade=80)
    # 拆成只有画面、只有声音两个文件（像 B 站给的那样），再合回来
    for kind in ("video", "audio"):
        with av.open(str(src)) as c, av.open(str(tmp_path / f"{kind}.mp4"), "w") as out:
            s = c.streams.get(**{kind: 0})[0]
            o = out.add_stream_from_template(s)
            for pk in c.demux(s):
                if pk.dts is not None:
                    pk.stream = o
                    out.mux(pk)
    dst = tmp_path / "out.mp4"
    mux(tmp_path / "video.mp4", tmp_path / "audio.mp4", dst)
    total = duration(dst)
    assert total["video"] == pytest.approx(3, abs=0.3) and total["audio"] == pytest.approx(3, abs=0.3)
    assert not (tmp_path / "out.mp4.part").exists()


def test_write_tags_keeps_streams_and_adds_custom_key(tmp_path: Path):
    f = tmp_path / "a.mp4"
    make_clip(f, seconds=2, shade=80)
    before = duration(f)
    write_tags(f, {"AIGC": '{"Label": "1"}'})
    with av.open(str(f)) as c:
        assert c.metadata.get("AIGC") == '{"Label": "1"}'
    assert duration(f) == before
    assert not (tmp_path / "a.mp4.part").exists()
