from __future__ import annotations

from typing import Any

from app.adapters.fixtures import FixtureStore, load_fixtures
from app.services.access import AccessContext


class MockTopologyAdapter:
    source_mode = "mock"
    source_system = "mock-topology"

    def __init__(self, store: FixtureStore | None = None):
        self.store = store or load_fixtures()

    def upstream_related(
        self,
        *,
        asset_id: str,
        rel_type: str,
        direction: str,
        asset_types: list[str],
        access: AccessContext,
        limit: int = 4,
    ) -> dict[str, Any]:
        if self.store.device_by_asset(asset_id, access) is None:
            return {
                "status": "error",
                "error_code": "asset_out_of_scope",
                "relations": [],
                "unknown_relations": [],
                "warnings": [],
            }
        raw_relations = self.store.topology_for(asset_id, rel_type, direction)
        relations: list[dict[str, Any]] = []
        warnings: list[str] = []
        for relation in raw_relations:
            if len(relations) >= limit:
                warnings.append("related_asset_limit_reached")
                break
            related_id = (
                relation["from_device_id"] if direction == "upstream" else relation["to_device_id"]
            )
            related_device = self.store.device_by_asset(related_id, access)
            if related_device is None:
                warnings.append("related_asset_out_of_scope")
                continue
            if related_device.get("type_code") not in asset_types:
                warnings.append(f"related_asset_type_mismatch:{related_id}")
                continue
            relations.append(
                {
                    "asset_id": related_device["asset_id"],
                    "device_code": related_device.get("device_code"),
                    "name": related_device.get("name"),
                    "type_code": related_device.get("type_code"),
                    "room_id": related_device.get("room_id"),
                    "relation_id": relation["relation_id"],
                    "rel_type": relation["rel_type"],
                    "direction": relation["direction"],
                    "raw_rel_type": relation["raw_rel_type"],
                    "path_ref": relation.get("path_ref"),
                    "temporal_basis": relation.get("temporal_basis"),
                    "source_system": relation.get("source_system", self.source_system),
                }
            )
        unknown = [
            {
                "relation_id": item["relation_id"],
                "raw_rel_type": item["raw_rel_type"],
                "path_ref": item.get("path_ref"),
                "source_system": item.get("source_system", self.source_system),
            }
            for item in self.store.unknown_topology_for(asset_id)
        ]
        if unknown:
            warnings.append("unknown_relation_present")
        return {
            "status": "success",
            "relations": relations,
            "unknown_relations": unknown,
            "warnings": warnings,
            "source_system": self.source_system,
            "snapshot": "current_registered_topology",
        }
