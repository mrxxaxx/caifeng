"""创建窗口：系统自带的 WebView2 显示 web/ 下的界面，界面通过 Api 调 Python。"""

from __future__ import annotations

import sys
import threading
import time
import winreg
from importlib.resources import files
from pathlib import Path

import webview

from caifeng import __version__, update
from caifeng.api import Api
from caifeng.config import DEFAULT_ROOT, ConfigStore

BG = {"light": "#EBEBEE", "dark": "#1C1C1F"}  # 和 style.css 的 --bg 一致，避免页面加载前闪白
TITLE = f"采风 - v{__version__}"
_mutex = None  # 进程活着就一直拿着，退出时系统自动释放


def _already_running(wait: float = 0) -> bool:
    """已经开着一个采风就把它切到前面，返回 True（两个进程会抢同一份浏览器 profile）。
    wait：自动更新后打开的新版，旧版正在退出，最多等这么多秒。"""
    import ctypes

    global _mutex
    kernel32, user32 = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.windll.user32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    deadline = time.monotonic() + wait
    while True:
        _mutex = kernel32.CreateMutexW(None, False, rf"Local\{DEFAULT_ROOT.name}-single-instance")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            return False
        if time.monotonic() >= deadline:
            break
        kernel32.CloseHandle(_mutex)
        time.sleep(0.3)
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE：最小化了就还原
        user32.SetForegroundWindow(hwnd)
    return True


def _system_is_dark() -> bool:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _remove_old(old: str) -> None:
    """便携版自动更新后：旧 exe 退出要一会儿（还要清理自己解压的临时文件），删不掉就隔一会儿再试。"""
    path = Path(old)
    if path.suffix.lower() != ".exe" or path.resolve() == Path(sys.executable).resolve():
        return
    for _ in range(60):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(1)


def run() -> None:
    after = sys.argv.index(update.AFTER_UPDATE) if update.AFTER_UPDATE in sys.argv else -1
    if _already_running(wait=20 if after >= 0 else 0):
        return
    if after >= 0 and after + 1 < len(sys.argv):  # 便携版带着旧 exe 的路径：删掉旧 exe
        threading.Thread(target=_remove_old, args=(sys.argv[after + 1],), daemon=True).start()
    elif after >= 0:  # 安装版：删掉下载的安装包
        threading.Thread(target=update.cleanup, daemon=True).start()
    api = Api(ConfigStore())
    look = api._settings.appearance
    dark = look == "dark" or (look == "system" and _system_is_dark())
    page = files("caifeng") / "web" / "index.html"
    window = webview.create_window(
        TITLE,
        url=str(page),
        js_api=api,
        width=1080,
        height=720,
        min_size=(920, 640),
        background_color=BG["dark" if dark else "light"],
    )
    api._attach(window)
    window.events.closed += api._shutdown  # 关窗口时顺手关掉后台打开的浏览器
    webview.start(gui="edgechromium", http_server=True)
