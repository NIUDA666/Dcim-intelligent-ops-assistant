from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.adapters.fixtures import FixtureStore, load_fixtures, parse_ts
from app.services.access import AccessContext


class MockDataError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _normalize_value(metric_code: str, value: Any) -> Any:
    if metric_code == "operating_state":
        if isinstance(value, (int, float)):
            return "running" if value >= 0.5 else "stopped"
        return value
    return value


class MockMonitoringAdapter:
    source_mode = "mock"
    source_system = "mock-monitoring"

    def __init__(self, store: FixtureStore | None = None):
        self.store = store or load_fixtures()

    def get_latest(
        self,
        *,
        asset_id: str,
        metric_code: str,
        point_id: str,
        query_time: datetime,
        access: AccessContext,
    ) -> dict[str, Any]:
        if self.store.device_by_asset(asset_id, access) is None:
            raise MockDataError("asset_out_of_scope", "设备不在授权范围内")
        series = self.store.series_for_point(point_id)
        if series is None:
            raise MockDataError("point_series_missing", "测点没有可用历史序列")
        candidates = [
            point
            for point in series["points"]
            if parse_ts(point["timestamp"]) <= query_time
        ]
        if not candidates:
            raise MockDataError("no_reading_before_query_time", "查询时点前没有读数")
        point = candidates[-1]
        timestamp = parse_ts(point["timestamp"])
        stale_after = timedelta(minutes=5)
        warnings: list[str] = []
        quality = point.get("quality", "good")
        if query_time - timestamp > stale_after:
            quality = "stale"
            warnings.append("latest_reading_stale")
        return {
            "asset_id": asset_id,
            "metric_code": metric_code,
            "point_id": point_id,
            "value": _normalize_value(metric_code, point["value"]),
            "timestamp": timestamp.isoformat(),
            "quality": quality,
            "unit": series.get("unit"),
            "warnings": warnings,
            "source_system": self.source_system,
        }

    def get_series(
        self,
        *,
        asset_id: str,
        metric_code: str,
        point_id: str,
        window_start: datetime,
        window_end: datetime,
        access: AccessContext,
    ) -> dict[str, Any]:
        if self.store.device_by_asset(asset_id, access) is None:
            raise MockDataError("asset_out_of_scope", "设备不在授权范围内")
        series = self.store.series_for_point(point_id)
        if series is None:
            raise MockDataError("point_series_missing", "测点没有可用历史序列")
        points = [
            {
                "timestamp": parse_ts(point["timestamp"]).isoformat(),
                "value": _normalize_value(metric_code, point["value"]),
                "quality": point.get("quality", "good"),
            }
            for point in series["points"]
            if window_start <= parse_ts(point["timestamp"]) < window_end
        ]
        interval = int(series.get("sampling_interval_seconds") or 60)
        expected = 0
        if window_end > window_start:
            expected = max(1, int((window_end - window_start).total_seconds() // interval))
        missing_ratio = 0.0 if expected == 0 else max(0.0, 1.0 - len(points) / expected)
        quality_counts: dict[str, int] = {}
        for point in points:
            quality_counts[point["quality"]] = quality_counts.get(point["quality"], 0) + 1
        complete = bool(series.get("complete", False)) and missing_ratio <= 0.05
        warnings: list[str] = []
        if not complete:
            warnings.append("series_incomplete")
        if window_end == window_start:
            warnings.append("empty_time_window")
        return {
            "asset_id": asset_id,
            "metric_code": metric_code,
            "point_id": point_id,
            "points": points,
            "unit": series.get("unit"),
            "sampling_interval_seconds": interval,
            "expected_points": expected,
            "actual_points": len(points),
            "missing_ratio": round(missing_ratio, 6),
            "quality_counts": quality_counts,
            "complete": complete,
            "warnings": warnings,
            "source_system": self.source_system,
        }

    def get_alarm_records(
        self,
        *,
        asset_ids: set[str],
        access: AccessContext,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        visible = {
            device["asset_id"]
            for device in self.store.visible_devices(access)
            if device["asset_id"] in asset_ids
        }
        # 同一 episode 的重复提醒合并为一条独立记录；恢复后再次触发使用新 episode_id。
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for episode in self.store.episodes_for(visible):
            occurred_at = parse_ts(episode["occurred_at"])
            if start is not None and occurred_at < start:
                continue
            if end is not None and occurred_at >= end:
                continue
            source_system = episode.get("source_system", "mock-alarm")
            key = (source_system, episode["episode_id"])
            record = grouped.get(key)
            if record is None:
                record = dict(episode)
                record["source_system"] = source_system
                record["notification_count"] = 1
                record["notification_times"] = [
                    (episode.get("notification_at") or episode["occurred_at"])
                ]
                grouped[key] = record
            else:
                notification_at = episode.get("notification_at") or episode["occurred_at"]
                if notification_at not in record["notification_times"]:
                    record["notification_times"].append(notification_at)
                record["notification_count"] = len(record["notification_times"])
        records: list[dict[str, Any]] = []
        for record in grouped.values():
            record["occurred_at"] = parse_ts(record["occurred_at"]).isoformat()
            record["recovered_at"] = (
                parse_ts(record["recovered_at"]).isoformat() if record.get("recovered_at") else None
            )
            record["notification_times"] = [
                parse_ts(value).isoformat() for value in record["notification_times"]
            ]
            record["asset_name"] = self.store.device_name(record["device_id"])
            definition = self.store.alarm_definition(record["alarm_type_id"])
            record["alarm_name"] = (
                definition.get("name") if definition else record["alarm_type_id"]
            )
            records.append(record)
        records.sort(key=lambda item: item["occurred_at"])
        return records
