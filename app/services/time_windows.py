"""时间窗口解析（§6.4、§10.2）。

所有窗口使用 Asia/Shanghai 时区的半开区间 [start, end)。平台若使用闭区间，
由适配层转换后再次过滤，不在业务层盲目减一毫秒。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.schemas.execution import TimeWindow
from app.settings import BUSINESS_TZ, settings


class UnsupportedTimeExpression(ValueError):
    pass


_CN_NUM = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _to_number(text: str) -> float:
    if text in ("半",):
        return 0.5
    if text.isdigit():
        return float(text)
    if text == "十":
        return 10.0
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    if len(text) == 1 and text in _CN_NUM:
        return float(_CN_NUM[text])
    raise UnsupportedTimeExpression(f"unsupported_number:{text}")


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_required")
    return value.astimezone(BUSINESS_TZ)


def resolve_window(expression: str | None, query_time: datetime) -> TimeWindow:
    """把明确的时间表达解析为半开窗口。

    不认识的表达直接报错，由上层补问；不能悄悄替换成“最近两小时”。
    """
    end = ensure_aware(query_time)
    text = (expression or "").strip()
    if not text:
        raise UnsupportedTimeExpression("time_expression_required")

    midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
    if text in {"今天", "今日"}:
        return TimeWindow(start=midnight, end=end)
    if text in {"昨天", "昨日"}:
        return TimeWindow(start=midnight - timedelta(days=1), end=midnight)
    if text in {"本周", "这周"}:
        start = midnight - timedelta(days=midnight.weekday())
        return TimeWindow(start=start, end=end)

    match = re.fullmatch(
        r"(?:最近|近|过去)\s*([0-9一二三四五六七八九十两半]+)\s*(小时|分钟|天)",
        text,
    )
    if match:
        amount = _to_number(match.group(1))
        unit = match.group(2)
        if unit == "小时":
            delta = timedelta(hours=amount)
        elif unit == "分钟":
            delta = timedelta(minutes=amount)
        else:
            delta = timedelta(days=amount)
        return TimeWindow(start=end - delta, end=end)

    raise UnsupportedTimeExpression(f"unsupported_time_expression:{text}")


def latest_analysis_window(query_time: datetime) -> TimeWindow:
    """演示配置中的显式分析窗口。

    最新值查询的“现在”不是完整分析窗口（§5.5.2）；窗口来自明确的演示配置，
    不从用户文本或模型输出推断。
    """
    end = ensure_aware(query_time)
    return TimeWindow(
        start=end - timedelta(seconds=settings.analysis_window_seconds),
        end=end,
    )


def window_dict(window: TimeWindow | None) -> dict[str, str] | None:
    if window is None:
        return None
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
    }


def parse_time_hint(text: str | None) -> str | None:
    """保留原始时间表达，供后端解析；不在模型/解析层换算时间。"""
    if not text:
        return None
    return text.strip()
