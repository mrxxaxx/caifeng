"""短剧、漫剧的集数范围校验。网页端 app.js 里有一份相同规则的实现，用来即时提示。"""

from __future__ import annotations


def check_episodes(start: int | None, end: int | None, total: int | None) -> str:
    """返回错误说明，没问题返回空字符串。end 为空表示到最后一集；total 为空表示还不知道总集数。"""
    if start is None or start < 1:
        return "起始集数要从 1 开始"
    if end is not None and end < start:
        return "结束集数不能比起始集数小"
    if total is not None and (end or start) > total:
        return f"这部剧只有 {total} 集"
    return ""
