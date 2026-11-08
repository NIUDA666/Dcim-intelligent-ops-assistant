from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.adapters.fixtures import parse_ts
from app.services.time_windows import (
    UnsupportedTimeExpression,
    ensure_aware,
    latest_analysis_window,
    resolve_window,
)
from app.settings import BUSINESS_TZ, settings


QUERY_TIME = parse_ts("2026-10-26T10:30:00+08:00")


def test_today_is_half_open_window_from_midnight() -> None:
    window = resolve_window("今天", QUERY_TIME)
    assert window.start == parse_ts("2026-10-26T00:00:00+08:00")
    assert window.end == QUERY_TIME
    assert window.end - window.start == timedelta(hours=10, minutes=30)


def test_yesterday_ends_at_today_midnight() -> None:
    window = resolve_window("昨天", QUERY_TIME)
    assert window.start == parse_ts("2026-10-25T00:00:00+08:00")
    assert window.end == parse_ts("2026-10-26T00:00:00+08:00")


def test_recent_chinese_hours() -> None:
    window = resolve_window("最近两小时", QUERY_TIME)
    assert window.start == parse_ts("2026-10-26T08:30:00+08:00")
    assert window.end == QUERY_TIME


def test_recent_arabic_minutes() -> None:
    window = resolve_window("过去30分钟", QUERY_TIME)
    assert window.start == parse_ts("2026-10-26T10:00:00+08:00")
    assert window.end == QUERY_TIME


def test_unsupported_expression_is_not_replaced_with_default() -> None:
    with pytest.raises(UnsupportedTimeExpression):
        resolve_window("上周", QUERY_TIME)
    with pytest.raises(UnsupportedTimeExpression):
        resolve_window("", QUERY_TIME)


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone_required"):
        ensure_aware(datetime(2026, 10, 26, 10, 30))


def test_latest_analysis_window_uses_explicit_demo_config() -> None:
    window = latest_analysis_window(QUERY_TIME)
    assert window.end == QUERY_TIME
    assert window.start == QUERY_TIME - timedelta(seconds=settings.analysis_window_seconds)
    assert window.start.tzinfo == BUSINESS_TZ
