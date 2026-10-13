"""采风：从网站下载视频、小说和剧集。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("caifeng")
except PackageNotFoundError:  # 打包成 exe 后可能没有元数据
    __version__ = "0.0.0"
