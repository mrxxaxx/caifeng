"""设置与目录。

设置文件固定在 %LOCALAPPDATA%\\caifeng\\config.json：它记录数据目录在哪，所以自己不能挪。
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "caifeng"
CONFIG_FILE = DEFAULT_ROOT / "config.json"


@dataclass
class VideoSettings:
    output_dir: str = ""  # 空 = 输出目录下的「视频」，里面再按平台分文件夹
    nomark: bool = False  # 豆包视频去掉「AI生成」标识（打开前界面会让用户确认）


@dataclass
class FanqieSettings:
    """番茄达人中心。小说是文字，短剧、漫剧是视频。"""

    output_dir: str = ""  # 空 = 输出目录下的「番茄」
    kind: str = "novel"  # novel 小说 / drama 短剧 / comic 漫剧
    scope: str = "free"  # 小说：free 免费章节 / all 全部章节
    delay: float = 1.0  # 章与章、集与集之间的间隔（秒），最少 1 秒
    save_mode: str = "split"  # 短剧、漫剧：split 分集 / merge 合并 / both 两者都要


@dataclass
class Settings:
    data_dir: str = ""  # 空 = DEFAULT_ROOT
    browser: str = ""  # edge / chrome；空 = 本机检测到的第一个
    appearance: str = "system"  # system / light / dark
    notice_agreed: bool = False  # 同意过免责声明：第一次要看满 15 秒才能点同意，以后弹出不用等
    output_dir: str = ""  # 空 = 「下载」文件夹下的「采风」；各工具在里面建自己的子文件夹
    video: VideoSettings = field(default_factory=VideoSettings)
    fanqie: FanqieSettings = field(default_factory=FanqieSettings)


def _from_dict[T](cls: type[T], data: Any) -> T:
    """按 dataclass 字段读取：缺的、多余的、类型不对的字段都忽略，用默认值。"""
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        default = getattr(obj, f.name)
        value = data[f.name]
        if dataclasses.is_dataclass(default):
            setattr(obj, f.name, _from_dict(type(default), value))
        elif isinstance(default, float) and isinstance(value, int | float) and not isinstance(value, bool):
            setattr(obj, f.name, float(value))
        elif isinstance(value, type(default)):
            setattr(obj, f.name, value)
    return obj


class ConfigStore:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path

    def load(self) -> Settings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return Settings()
        return _from_dict(Settings, data)

    def save(self, settings: Settings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dataclasses.asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # 先写临时文件再替换，写到一半崩溃也不会损坏原设置


def downloads_dir() -> Path:
    """用户的「下载」文件夹。用户可能把它挪到别的盘，所以向系统查，查不到再用 ~/Downloads。"""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        u = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        buf = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buf)) == 0:
            path = Path(buf.value)
            ctypes.windll.ole32.CoTaskMemFree(buf)
            return path
    except AttributeError, OSError:
        pass
    return Path.home() / "Downloads"


TOOL_DIRS = {"video": "视频", "fanqie": "番茄"}


def output_root(settings: Settings, downloads: Path | None = None) -> Path:
    """总输出目录：设置里填了就用设置的，没填就是「下载」文件夹下的「采风」。"""
    if settings.output_dir:
        return Path(settings.output_dir)
    return (downloads or downloads_dir()) / "采风"


def tool_dir(settings: Settings, tool: str, downloads: Path | None = None) -> Path:
    """某个工具的保存位置：工具页上单独改过就用它，否则是总输出目录下的子文件夹。"""
    override = getattr(settings, tool).output_dir
    if override:
        return Path(override)
    return output_root(settings, downloads) / TOOL_DIRS[tool]


@dataclass(frozen=True)
class Paths:
    root: Path

    @classmethod
    def from_settings(cls, settings: Settings, default_root: Path = DEFAULT_ROOT) -> Paths:
        return cls(Path(settings.data_dir) if settings.data_dir else default_root)

    def profile(self, browser: str, site: str) -> Path:
        """每个浏览器、每个站点一个 profile：同一个 profile 同时只能被一个进程打开，分开后各站点互不阻塞。"""
        return self.root / "profiles" / browser / site
