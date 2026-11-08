from __future__ import annotations

from datetime import datetime

import pytest

from app.adapters.fixtures import parse_ts
from app.services.alarm_count import (
    AlarmEpisode,
    count_active,
    count_occurrences,
    count_recovered,
)

SOURCE = "mock-alarm"
ALARM_TYPE = "alarm_ac001_supply_high"
DEVICE = "asset_ac001"


def episode(
    episode_id: str,
    occurred_at: str,
    recovered_at: str | None = None,
) -> AlarmEpisode:
    return AlarmEpisode(
        source_system=SOURCE,
        episode_id=episode_id,
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        occurred_at=parse_ts(occurred_at),
        recovered_at=parse_ts(recovered_at) if recovered_at else None,
    )


@pytest.fixture()
def episodes() -> list[AlarmEpisode]:
    return [
        episode("ep_001", "2026-10-26T09:00:00+08:00", "2026-10-26T09:10:00+08:00"),
        episode("ep_002", "2026-10-26T10:10:00+08:00"),
        episode("ep_003", "2026-10-26T10:20:00+08:00", "2026-10-26T10:25:00+08:00"),
    ]


def test_new_occurrences_use_first_occurrence_in_window(episodes) -> None:
    outcome = count_occurrences(
        episodes,
        source_system=SOURCE,
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        start=parse_ts("2026-10-26T10:00:00+08:00"),
        end=parse_ts("2026-10-26T10:30:00+08:00"),
        complete=True,
    )
    assert outcome["count"] == 2
    assert outcome["observed_distinct"] == 2
    assert outcome["policy"] == "new_occurrences_in_window"


def test_duplicate_episode_records_do_not_increase_count(episodes) -> None:
    duplicated = [*episodes, episodes[1], episodes[1]]
    outcome = count_occurrences(
        duplicated,
        source_system=SOURCE,
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        start=parse_ts("2026-10-26T10:00:00+08:00"),
        end=parse_ts("2026-10-26T10:30:00+08:00"),
        complete=True,
    )
    assert outcome["count"] == 2


def test_active_and_new_occurrence_are_different_policies(episodes) -> None:
    active = count_active(
        episodes,
        source_system=SOURCE,
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        at=parse_ts("2026-10-26T10:30:00+08:00"),
    )
    assert active["count"] == 1
    assert active["policy"] == "active_at_query_time"
    assert [item.episode_id for item in active["episodes"]] == ["ep_002"]


def test_recovered_count_uses_recovery_time(episodes) -> None:
    outcome = count_recovered(
        episodes,
        source_system=SOURCE,
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        start=parse_ts("2026-10-26T10:00:00+08:00"),
        end=parse_ts("2026-10-26T10:30:00+08:00"),
        complete=True,
    )
    assert outcome["count"] == 1
    assert outcome["episodes"][0].episode_id == "ep_003"


def test_incomplete_source_does_not_return_a_total(episodes) -> None:
    outcome = count_occurrences(
        episodes,
        source_system=SOURCE,
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        start=parse_ts("2026-10-26T10:00:00+08:00"),
        end=parse_ts("2026-10-26T10:30:00+08:00"),
        complete=False,
    )
    assert outcome["count"] is None
    assert outcome["observed_distinct"] == 2
    assert outcome["status"] == "partial"


def test_conflicting_episode_identity_is_rejected(episodes) -> None:
    conflicting = [
        *episodes,
        episode("ep_002", "2026-10-26T10:11:00+08:00"),
    ]
    with pytest.raises(ValueError, match="conflicting_episode_identity"):
        count_occurrences(
            conflicting,
            source_system=SOURCE,
            device_id=DEVICE,
            alarm_type_id=ALARM_TYPE,
            start=parse_ts("2026-10-26T10:00:00+08:00"),
            end=parse_ts("2026-10-26T10:30:00+08:00"),
            complete=True,
        )


def test_naive_timestamps_are_rejected() -> None:
    naive = AlarmEpisode(
        source_system=SOURCE,
        episode_id="ep_naive",
        device_id=DEVICE,
        alarm_type_id=ALARM_TYPE,
        occurred_at=datetime(2026, 10, 26, 10, 0),
    )
    with pytest.raises(ValueError, match="occurred_at_timezone_required"):
        count_active(
            [naive],
            source_system=SOURCE,
            device_id=DEVICE,
            alarm_type_id=ALARM_TYPE,
            at=parse_ts("2026-10-26T10:30:00+08:00"),
        )
