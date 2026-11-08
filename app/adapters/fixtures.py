from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.access import AccessContext
from app.settings import BUSINESS_TZ, settings


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TZ)
    return parsed.astimezone(BUSINESS_TZ)


@dataclass(frozen=True)
class FixtureStore:
    devices: tuple[dict[str, Any], ...]
    point_bindings: tuple[dict[str, Any], ...]
    alarm_definitions: tuple[dict[str, Any], ...]
    alarm_episodes: tuple[dict[str, Any], ...]
    timeseries: dict[str, dict[str, Any]]
    topology_relations: tuple[dict[str, Any], ...]
    knowledge_docs: tuple[dict[str, Any], ...]
    faq_entries: tuple[dict[str, Any], ...]
    work_orders: tuple[dict[str, Any], ...]

    def visible_devices(self, access: AccessContext) -> list[dict[str, Any]]:
        return [device for device in self.devices if access.allows_device(device)]

    def device_by_asset(self, asset_id: str, access: AccessContext) -> dict[str, Any] | None:
        for device in self.devices:
            if device["asset_id"] == asset_id and access.allows_device(device):
                return device
        return None

    def devices_by_room(self, room_id: str, access: AccessContext) -> list[dict[str, Any]]:
        return [
            device
            for device in self.devices
            if device.get("room_id") == room_id and access.allows_device(device)
        ]

    def device_name(self, asset_id: str) -> str:
        for device in self.devices:
            if device["asset_id"] == asset_id:
                return device.get("name") or device.get("device_code") or asset_id
        return asset_id

    def points_for(self, asset_id: str, metric_code: str) -> list[dict[str, Any]]:
        return [
            binding
            for binding in self.point_bindings
            if binding["asset_id"] == asset_id and binding["metric_code"] == metric_code
        ]

    def series_for_point(self, point_id: str) -> dict[str, Any] | None:
        return self.timeseries.get(point_id)

    def alarm_definitions_for(self, asset_id: str) -> list[dict[str, Any]]:
        return [item for item in self.alarm_definitions if item["asset_id"] == asset_id]

    def alarm_definition(self, alarm_type_id: str) -> dict[str, Any] | None:
        for item in self.alarm_definitions:
            if item["alarm_type_id"] == alarm_type_id:
                return item
        return None

    def episodes_for(self, asset_ids: set[str]) -> list[dict[str, Any]]:
        return [item for item in self.alarm_episodes if item["device_id"] in asset_ids]

    def topology_for(self, asset_id: str, rel_type: str, direction: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.topology_relations
            if item["query_asset_id"] == asset_id
            and item["rel_type"] == rel_type
            and item["direction"] == direction
        ]

    def unknown_topology_for(self, asset_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.topology_relations
            if item["query_asset_id"] == asset_id and item["rel_type"] == "unknown"
        ]

    def work_orders_for(self, asset_id: str) -> list[dict[str, Any]]:
        return [item for item in self.work_orders if item["asset_id"] == asset_id]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_fixtures() -> FixtureStore:
    base = settings.fixtures_dir
    return FixtureStore(
        devices=tuple(_read_json(base / "devices.json")),
        point_bindings=tuple(_read_json(base / "point_bindings.json")),
        alarm_definitions=tuple(_read_json(base / "alarm_definitions.json")),
        alarm_episodes=tuple(_read_json(base / "alarm_episodes.json")),
        timeseries=_read_json(base / "timeseries.json"),
        topology_relations=tuple(_read_json(base / "topology_relations.json")),
        knowledge_docs=tuple(_read_json(base / "knowledge_docs.json")),
        faq_entries=tuple(_read_json(base / "faq_entries.json")),
        work_orders=tuple(_read_json(base / "work_orders.json")),
    )
