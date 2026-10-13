# 采风

Windows 小工具：从网站下载视频、小说和剧集。

- **视频**：抖音、快手、小红书、哔哩哔哩、豆包
- **番茄**：番茄达人中心的小说、短剧、漫剧

[去下载](https://github.com/mrxxaxx/caifeng/releases)

## 使用

- 下载用本机的 Edge 或 Chrome。视频不用登录；番茄第一次要在弹出的浏览器里登录。
- 设置、下载列表和登录数据都在 `%LOCALAPPDATA%\caifeng`。

## 开发

需要 Python 3.14 和 [uv](https://docs.astral.sh/uv/)。

```
uv sync
uv run caifeng          # 运行
uv run pytest           # 测试
uv run ruff check .
uv run python scripts/build.py   # 打包
```

打包出两个文件，都在 `dist/`：便携版 `caifeng-v版本号-portable.exe`（单个文件，拷走就能用），和安装包 `caifeng-v版本号-setup.exe`。安装包要先装 [Inno Setup](https://jrsoftware.org/isinfo.php)，版本号取自 `pyproject.toml`。

发版：改 `pyproject.toml` 里的版本号，推一个同名标签（如 `v0.2.0`），GitHub Actions 会测试、打包并发布到 Releases。

## 许可证

代码按 [MIT](LICENSE) 开源。

## 免责声明

本项目仅供学习交流，请勿用于商业、非法用途。
