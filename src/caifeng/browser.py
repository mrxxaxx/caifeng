"""本机浏览器：检测 Edge / Chrome，用独立的 profile 启动。

不用日常浏览器的 profile：同一个 profile 只能被一个进程打开，而且 Chrome / Edge 136 起禁止对默认用户目录开远程调试。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DOWNLOAD_LINKS = {
    "Microsoft Edge": "https://www.microsoft.com/edge",
    "Google Chrome": "https://www.google.cn/chrome/",
}


@dataclass(frozen=True)
class Browser:
    key: str
    name: str
    channel: str  # Playwright channel
    candidates: tuple[str, ...]


BROWSERS = (
    Browser(
        "edge",
        "Microsoft Edge",
        "msedge",
        (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "/Applications/Microsoft Edge.app",  # macOS
            str(Path.home() / "Applications/Microsoft Edge.app"),
        ),
    ),
    Browser(
        "chrome",
        "Google Chrome",
        "chrome",
        (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
            "/Applications/Google Chrome.app",  # macOS
            str(Path.home() / "Applications/Google Chrome.app"),
        ),
    ),
)


def installed() -> list[Browser]:
    return [b for b in BROWSERS if any(Path(p).exists() for p in b.candidates)]


def pick(key: str, available: list[Browser] | None = None) -> Browser | None:
    """设置里选的浏览器；没选或者那个没装，就用检测到的第一个；一个都没有返回 None。"""
    available = installed() if available is None else available
    return next((b for b in available if b.key == key), available[0] if available else None)


DIRECT = ["--no-proxy-server"]  # 不走系统代理（国内平台走境外代理时有的拿不到数据，见 sites/video.py）
_ua: dict[str, str] = {}


def headless_ua(pw, browser: Browser) -> str:
    """无头浏览器的 UA 里有 HeadlessChrome，快手等网站认出来就不给数据；换成 Chrome，版本号不变。"""
    if browser.key not in _ua:
        b = pw.chromium.launch(channel=browser.channel, headless=True)
        try:
            _ua[browser.key] = b.new_page().evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
        finally:
            b.close()
    return _ua[browser.key]


def launch(pw, browser: Browser, profile: Path, headless: bool, direct: bool = False):
    """用专用 profile 启动持久化上下文（登录状态保存在 profile 里）。direct：不走系统代理。"""
    profile.mkdir(parents=True, exist_ok=True)
    ctx = pw.chromium.launch_persistent_context(
        str(profile),
        channel=browser.channel,
        headless=headless,
        viewport=None if not headless else {"width": 1280, "height": 860},
        no_viewport=not headless,
        locale="zh-CN",
        user_agent=headless_ua(pw, browser) if headless else None,
        args=DIRECT if direct else [],
        # Playwright 默认带 --no-sandbox，Edge/Chrome 会弹「不受支持的命令行标志」提示条；开沙箱就没有了
        chromium_sandbox=True,
        # 不加 --enable-automation：去掉「受自动测试软件控制」提示条
        ignore_default_args=["--enable-automation"],
    )
    # 隐藏 navigator.webdriver。不用 --disable-blink-features=AutomationControlled，那个会弹「不受支持的命令行标记」
    ctx.add_init_script(HIDE_WEBDRIVER)
    return ctx


HIDE_WEBDRIVER = "Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false })"


def launch_temp(pw, browser: Browser, direct: bool = False):
    """另起一个不用 profile 的无头浏览器（用户开着窗口时后台下载用），返回 (browser, context)。"""
    b = pw.chromium.launch(
        channel=browser.channel,
        headless=True,
        args=DIRECT if direct else [],
        chromium_sandbox=True,
        ignore_default_args=["--enable-automation"],
    )
    ctx = b.new_context(locale="zh-CN", viewport={"width": 1280, "height": 860}, user_agent=headless_ua(pw, browser))
    ctx.add_init_script(HIDE_WEBDRIVER)
    return b, ctx
