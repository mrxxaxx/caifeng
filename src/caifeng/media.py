"""音视频处理（PyAV，自带 ffmpeg 库，不需要另装 ffmpeg）：番茄短剧、漫剧合并成一个视频；B 站画面和声音合成一个文件。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path


def read_tags(url: str, keys: Sequence[str], user_agent: str = "", timeout: float = 15) -> dict[str, str]:
    """读一个在线视频文件的元数据标签（只读文件头，不下载整个文件）。"""
    import av

    options = {"timeout": str(int(timeout * 1_000_000))}
    if user_agent:
        options["user_agent"] = user_agent
    with av.open(url, options=options) as c:
        return {k: c.metadata[k] for k in keys if k in c.metadata}


def write_tags(path: Path, tags: dict[str, str]) -> None:
    """给 MP4 加上元数据标签，只搬运数据不重新编码。自定义的键（比如 AIGC）要 use_metadata_tags 才会写进去。"""
    import av

    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        with (
            av.open(str(path)) as src,
            av.open(str(part), "w", format="mp4", options={"movflags": "use_metadata_tags"}) as out,
        ):
            out.metadata.update(dict(src.metadata))
            out.metadata.update(tags)
            streams = {s: out.add_stream_from_template(s) for s in src.streams if s.type in ("video", "audio")}
            for pk in src.demux(list(streams)):
                if pk.dts is None:  # 结尾的空包
                    continue
                pk.stream = streams[pk.stream]
                out.mux(pk)
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def mux(video: Path, audio: Path, dst: Path) -> None:
    """把只有画面的文件和只有声音的文件合成一个 MP4，只搬运数据不重新编码。

    两边按时间先后交替写入（只先写完画面再写声音的话，封装器要把整段画面攒在内存里等声音）。
    """
    import av

    dst = Path(dst)
    part = dst.with_name(dst.name + ".part")
    with av.open(str(video)) as v, av.open(str(audio)) as a, av.open(str(part), "w", format="mp4") as out:
        sources = [(v, v.streams.video[0]), (a, a.streams.audio[0])]
        targets = {s: out.add_stream_from_template(s) for _, s in sources}
        packets = [c.demux(s) for c, s in sources]
        heads = [next(p, None) for p in packets]  # 每路当前待写的包
        while True:
            alive = [(i, pk) for i, pk in enumerate(heads) if pk is not None]
            if not alive:
                break
            i, pk = min(alive, key=lambda x: (x[1].dts if x[1].dts is not None else -1) * x[1].time_base)
            heads[i] = next(packets[i], None)
            if pk.dts is None:  # 结尾的空包
                continue
            pk.stream = targets[sources[i][1]]
            out.mux(pk)
    part.replace(dst)


def concat_mp4(inputs: Sequence[Path], dst: Path, should_stop: Callable[[], bool] | None = None) -> bool:
    """把几段同样编码的 MP4 首尾相接成一个文件，只搬运数据不重新编码，所以很快。

    以第一段的音视频轨为准，后面每段的时间戳接在前一段末尾。中途停止返回 False，不留半截文件。
    """
    import av

    dst = Path(dst)
    part = dst.with_name(dst.name + ".part")
    with av.open(str(inputs[0])) as first, av.open(str(part), "w", format="mp4") as out:
        kinds = [s.type for s in first.streams if s.type in ("video", "audio")]
        streams = {k: out.add_stream_from_template(first.streams.get(**{k: 0})[0]) for k in kinds}
        offset = {k: Fraction(0) for k in kinds}  # 秒
        last_dts = dict.fromkeys(kinds)  # 输出里每条轨上一个包的 dts，必须严格递增
        stopped = False
        for path in inputs:
            if should_stop and should_stop():
                stopped = True
                break
            with av.open(str(path)) as src:
                ends = dict.fromkeys(kinds, Fraction(0))
                ins = [s for s in src.streams if s.type in streams]
                shift: dict[str, int] = {}
                for packet in src.demux(ins):
                    if packet.dts is None:  # 结尾的空包
                        continue
                    kind = packet.stream.type
                    tb = packet.stream.time_base
                    if kind not in shift:
                        shift[kind] = int(offset[kind] / tb)
                        # 有 B 帧的视频开头 dts 是负的，平移后可能不比上一段最后一个包大，整体再往后挪一点
                        if last_dts[kind] is not None and packet.dts + shift[kind] <= last_dts[kind]:
                            shift[kind] = last_dts[kind] + 1 - packet.dts
                    pts = packet.pts if packet.pts is not None else packet.dts
                    ends[kind] = max(ends[kind], (pts + shift[kind] + (packet.duration or 0)) * tb)
                    packet.pts = None if packet.pts is None else packet.pts + shift[kind]
                    packet.dts += shift[kind]
                    last_dts[kind] = packet.dts
                    packet.stream = streams[kind]
                    out.mux(packet)
                # 下一段从这一段最长的轨结束处接上，音画不会越接越错位
                longest = max(ends.values())
                for k in kinds:
                    offset[k] = longest
    if stopped:
        part.unlink(missing_ok=True)
        return False
    part.replace(dst)
    return True
