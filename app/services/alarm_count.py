"""告警 episode 统计（§6.6、§10.3）。

口径严格区分：
- new：窗口内首次发生的独立 episode；
- active：查询时刻仍在活动的 episode；
- recovered：窗口内恢复的 episode。
重复提醒、确认消息不构成新 episode；恢复后再次触发必须有新的 episode_id。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class AlarmEpisode:
    source_system: str
    episode_id: str
    device_id: str
    alarm_type_id: str
    occurred_at: datetime
    recovered_at: datetime | None = None
    status: str = "active"


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field}_timezone_required")


def _validate_episode_identity(episodes: Iterable[AlarmEpisode]) -> dict[tuple[str, str], tuple]:
    identities: dict[tuple[str, str], tuple] = {}
    for event in episodes:
        if not event.episode_id:
            raise ValueError("missing_episode_id")
        _require_aware(event.occurred_at, "occurred_at")
        if event.recovered_at is not None:
            _require_aware(event.recovered_at, "recovered_at")
            if event.recovered_at < event.occurred_at:
                raise ValueError("invalid_recovery_time")
        identity = (event.source_system, event.episode_id)
        signature = (
            event.device_id,
            event.alarm_type_id,
            event.occurred_at,
            event.recovered_at,
        )
        if identity in identities and identities[identity] != signature:
            raise ValueError("conflicting_episode_identity")
        identities[identity] = signature
    return identities


def _dedupe_episodes(episodes: Iterable[AlarmEpisode]) -> list[AlarmEpisode]:
    seen: dict[tuple[str, str], AlarmEpisode] = {}
    for event in episodes:
        seen.setdefault((event.source_system, event.episode_id), event)
    return list(seen.values())


def count_occurrences(
    episodes: list[AlarmEpisode],
    *,
    source_system: str,
    device_id: str | None,
    alarm_type_id: str,
    start: datetime,
    end: datetime,
    complete: bool,
) -> dict:
    """统计窗口内首次发生的独立 episode（policy=new_occurrences_in_window）。"""
    for stamp, name in ((start, "start"), (end, "end")):
        _require_aware(stamp, name)
    if start > end:
        raise ValueError("invalid_window")
    _validate_episode_identity(episodes)

    identities: dict[tuple[str, str], tuple] = {}
    matched: set[tuple[str, str]] = set()
    for event in _dedupe_episodes(episodes):
        if event.source_system != source_system:
            continue
        identity = (event.source_system, event.episode_id)
        signature = (
            event.device_id,
            event.alarm_type_id,
            event.occurred_at,
            event.recovered_at,
        )
        identities[identity] = signature
        if device_id is not None and event.device_id != device_id:
            continue
        if event.alarm_type_id != alarm_type_id:
            continue
        if start <= event.occurred_at < end:
            matched.add(identity)

    observed = len(matched)
    return {
        "status": "success" if complete else "partial",
        "count": observed if complete else None,
        "observed_distinct": observed,
        "complete": complete,
        "policy": "new_occurrences_in_window",
    }


def count_active(
    episodes: list[AlarmEpisode],
    *,
    source_system: str,
    device_id: str | None,
    alarm_type_id: str,
    at: datetime,
) -> dict:
    """查询时刻仍在活动的 episode；不能与“今日新发生”混用。"""
    _require_aware(at, "at")
    _validate_episode_identity(episodes)
    matched = []
    for event in _dedupe_episodes(episodes):
        if event.source_system != source_system:
            continue
        if device_id is not None and event.device_id != device_id:
            continue
        if event.alarm_type_id != alarm_type_id:
            continue
        if event.occurred_at < at and (event.recovered_at is None or event.recovered_at > at):
            matched.append(event)
    return {
        "status": "success",
        "count": len(matched),
        "observed_distinct": len(matched),
        "complete": True,
        "policy": "active_at_query_time",
        "episodes": matched,
    }


def count_recovered(
    episodes: list[AlarmEpisode],
    *,
    source_system: str,
    device_id: str | None,
    alarm_type_id: str,
    start: datetime,
    end: datetime,
    complete: bool,
) -> dict:
    _require_aware(start, "start")
    _require_aware(end, "end")
    if start > end:
        raise ValueError("invalid_window")
    _validate_episode_identity(episodes)
    matched = []
    for event in _dedupe_episodes(episodes):
        if event.source_system != source_system:
            continue
        if device_id is not None and event.device_id != device_id:
            continue
        if event.alarm_type_id != alarm_type_id:
            continue
        if event.recovered_at is not None and start <= event.recovered_at < end:
            matched.append(event)
    return {
        "status": "success" if complete else "partial",
        "count": len(matched) if complete else None,
        "observed_distinct": len(matched),
        "complete": complete,
        "policy": "recovered_in_window",
        "episodes": matched,
    }
