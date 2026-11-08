from __future__ import annotations

from datetime import datetime

from app.adapters.fixtures import parse_ts
from app.settings import BUSINESS_TZ, settings


class Clock:
    """请求时钟。

    演示模式固定到模拟数据锚点，保证同一输入可回放；fixed_query_time 会写入
    执行计划、结果和报告，页面不会把模拟锚点伪装成实时时间。
    """

    def now(self) -> datetime:
        if settings.demo_fixed_now:
            return parse_ts(settings.demo_fixed_now)
        return datetime.now(tz=BUSINESS_TZ)


clock = Clock()
