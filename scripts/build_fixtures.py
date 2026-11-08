"""生成固定模拟数据。

所有数据均为演示样本，不代表真实平台接口字段或现场运行限值。
固定锚点：2026-10-26 10:30:00 Asia/Shanghai。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
BASE = Path(__file__).resolve().parent.parent
FIX = BASE / "fixtures"
FIX.mkdir(exist_ok=True)

ANCHOR = datetime(2026, 10, 26, 10, 30, tzinfo=TZ)
START = datetime(2026, 10, 26, 8, 30, tzinfo=TZ)


def iso(value: datetime) -> str:
    return value.isoformat()


devices = [
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac001",
        "device_code": "AC001",
        "name": "精密空调 AC001",
        "site_id": "site_01",
        "room_id": "room_01",
        "room_name": "一号机房",
        "room_aliases": ["一号机房", "1号机房", "机房一"],
        "type_code": "precision_air_conditioner",
        "model_code": "CRAC-X1",
        "cooling_mode": "chilled_water",
        "capabilities": [
            "chilled_water",
            "supply_air_temperature",
            "return_air_temperature",
            "operating_state",
        ],
        "aliases": ["AC001", "AC-001", "1号空调"],
        "status": "active",
    },
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac002",
        "device_code": "AC002",
        "name": "精密空调 AC002",
        "site_id": "site_01",
        "room_id": "room_01",
        "room_name": "一号机房",
        "room_aliases": ["一号机房", "1号机房", "机房一"],
        "type_code": "precision_air_conditioner",
        "model_code": "CRAC-X1",
        "cooling_mode": "chilled_water",
        "capabilities": ["chilled_water", "supply_air_temperature", "operating_state"],
        "aliases": ["AC002", "2号空调"],
        "status": "active",
    },
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac003",
        "device_code": "AC003",
        "name": "精密空调 AC003",
        "site_id": "site_01",
        "room_id": "room_01",
        "room_name": "一号机房",
        "room_aliases": ["一号机房", "1号机房", "机房一"],
        "type_code": "precision_air_conditioner",
        "model_code": "CRAC-X1",
        "cooling_mode": "chilled_water",
        "capabilities": ["chilled_water", "supply_air_temperature", "operating_state"],
        "aliases": ["AC003", "3号空调"],
        "status": "active",
    },
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac001_r2",
        "device_code": "AC001",
        "name": "精密空调 AC001（二号机房）",
        "site_id": "site_01",
        "room_id": "room_02",
        "room_name": "二号机房",
        "room_aliases": ["二号机房", "2号机房", "机房二"],
        "type_code": "precision_air_conditioner",
        "model_code": "CRAC-X1",
        "cooling_mode": "chilled_water",
        "capabilities": ["chilled_water", "supply_air_temperature", "operating_state"],
        "aliases": ["AC001", "AC-001"],
        "status": "active",
    },
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac004",
        "device_code": "AC004",
        "name": "精密空调 AC004",
        "site_id": "site_01",
        "room_id": "room_02",
        "room_name": "二号机房",
        "room_aliases": ["二号机房", "2号机房", "机房二"],
        "type_code": "precision_air_conditioner",
        "model_code": "CRAC-DX2",
        "cooling_mode": "dx",
        "capabilities": ["supply_air_temperature", "operating_state"],
        "aliases": ["AC004", "4号空调"],
        "status": "active",
    },
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_chiller_01",
        "device_code": "CH001",
        "name": "冷水机组 CH001",
        "site_id": "site_01",
        "room_id": "room_00",
        "room_name": "冷源机房",
        "room_aliases": ["冷源机房"],
        "type_code": "chiller",
        "model_code": "CH-200",
        "cooling_mode": "chilled_water",
        "capabilities": ["chilled_water_supply_temperature"],
        "aliases": ["CH001", "冷机1"],
        "status": "active",
    },
    {
        "tenant_id": "tenant_demo",
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_power_01",
        "device_code": "PD001",
        "name": "配电柜 PD001",
        "site_id": "site_01",
        "room_id": "room_00",
        "room_name": "配电间",
        "room_aliases": ["配电间"],
        "type_code": "power_distribution",
        "model_code": "PD-100",
        "capabilities": ["operating_state", "output_voltage"],
        "aliases": ["PD001", "配电柜1"],
        "status": "active",
    },
]

point_bindings = [
    {
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac001",
        "metric_code": "supply_air_temperature",
        "channel": "default",
        "point_id": "point_asset_ac001_supply_temp",
        "unit": "℃",
        "aggregation": "raw",
        "expected_interval_seconds": 60,
    },
    {
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac001",
        "metric_code": "return_air_temperature",
        "channel": "default",
        "point_id": "point_ac001_return_temp",
        "unit": "℃",
        "aggregation": "raw",
        "expected_interval_seconds": 60,
    },
    {
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_ac001",
        "metric_code": "operating_state",
        "channel": "default",
        "point_id": "point_ac001_state",
        "unit": None,
        "aggregation": "raw",
        "expected_interval_seconds": 60,
    },
    *[
        {
            "platform_instance_id": "mock-platform-01",
            "asset_id": asset_id,
            "metric_code": "supply_air_temperature",
            "channel": "default",
            "point_id": f"point_{asset_id}_supply_temp",
            "unit": "℃",
            "aggregation": "raw",
            "expected_interval_seconds": 60,
        }
        for asset_id in ["asset_ac002", "asset_ac003", "asset_ac001_r2", "asset_ac004"]
    ],
    {
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_chiller_01",
        "metric_code": "chilled_water_supply_temperature",
        "channel": "default",
        "point_id": "point_chiller_supply_temp",
        "unit": "℃",
        "aggregation": "raw",
        "expected_interval_seconds": 60,
    },
    {
        "platform_instance_id": "mock-platform-01",
        "asset_id": "asset_power_01",
        "metric_code": "operating_state",
        "channel": "default",
        "point_id": "point_power_state",
        "unit": None,
        "aggregation": "raw",
        "expected_interval_seconds": 60,
    },
]

alarm_type_by_asset = {
    "asset_ac001": ("alarm_ac001_supply_high", ["高温告警", "送风温度高", "高温"]),
    "asset_ac002": ("alarm_ac002_supply_high", ["高温告警", "送风温度高", "高温"]),
    "asset_ac003": ("alarm_ac003_supply_high", ["高温告警", "送风温度高", "高温"]),
    "asset_ac001_r2": ("alarm_ac001_r2_supply_high", ["高温告警", "送风温度高", "高温"]),
    "asset_ac004": ("alarm_ac004_supply_high", ["高温告警", "送风温度高", "高温"]),
}

alarm_definitions = [
    {
        "asset_id": asset_id,
        "alarm_type_id": alarm_type_id,
        "metric_code": "supply_air_temperature",
        "anomaly_kind": "high",
        "name": "送风温度高",
        "aliases": aliases,
        "lifecycle_mode": "occurrence_recovery",
        "version": "demo-1",
    }
    for asset_id, (alarm_type_id, aliases) in alarm_type_by_asset.items()
]

alarm_episodes = [
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_001",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-26T08:10:00+08:00",
        "recovered_at": "2026-10-26T08:40:00+08:00",
        "status": "recovered",
        "record_type": "lifecycle",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_002",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-26T09:40:00+08:00",
        "recovered_at": "2026-10-26T09:50:00+08:00",
        "status": "recovered",
        "record_type": "lifecycle",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_003",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-26T10:10:00+08:00",
        "recovered_at": None,
        "status": "active",
        "record_type": "lifecycle",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_003",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-26T10:10:00+08:00",
        "recovered_at": None,
        "status": "active",
        "record_type": "notification",
        "notification_at": "2026-10-26T10:15:00+08:00",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_003",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-26T10:10:00+08:00",
        "recovered_at": None,
        "status": "active",
        "record_type": "notification",
        "notification_at": "2026-10-26T10:20:00+08:00",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_004",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-25T23:58:00+08:00",
        "recovered_at": "2026-10-26T00:05:00+08:00",
        "status": "recovered",
        "record_type": "lifecycle",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_005",
        "device_id": "asset_ac001",
        "alarm_type_id": "alarm_ac001_supply_high",
        "occurred_at": "2026-10-25T22:00:00+08:00",
        "recovered_at": "2026-10-25T22:30:00+08:00",
        "status": "recovered",
        "record_type": "lifecycle",
    },
    {
        "source_system": "mock-alarm",
        "episode_id": "ep_006",
        "device_id": "asset_ac002",
        "alarm_type_id": "alarm_ac002_supply_high",
        "occurred_at": "2026-10-26T09:00:00+08:00",
        "recovered_at": "2026-10-26T09:20:00+08:00",
        "status": "recovered",
        "record_type": "lifecycle",
    },
]


def series_point(ts: datetime, value: float, quality: str = "good") -> dict:
    return {"timestamp": iso(ts), "value": round(value, 3), "quality": quality}


def build_series() -> dict:
    result: dict[str, dict] = {}
    count = 121
    for asset_id in ["asset_ac001", "asset_ac002", "asset_ac003", "asset_ac001_r2", "asset_ac004"]:
        key = f"point_{asset_id}_supply_temp"
        values = []
        for i in range(count):
            ts = START + timedelta(minutes=i)
            if asset_id == "asset_ac001":
                if i < 100:
                    value = 24.0 + i * 0.005 + 0.05 * ((i % 7) - 3) / 3
                elif i < 110:
                    value = 24.5 + (i - 100) * 0.05
                elif i < 115:
                    value = 25.0
                else:
                    value = 29.0 + (i - 115) * 0.02
            else:
                value = 24.0 + 0.05 * ((i % 7) - 3) / 3
            values.append(series_point(ts, value))
        result[key] = {
            "unit": "℃",
            "aggregation": "raw",
            "sampling_interval_seconds": 60,
            "complete": True,
            "points": values,
        }

    result["point_ac001_return_temp"] = {
        "unit": "℃",
        "aggregation": "raw",
        "sampling_interval_seconds": 60,
        "complete": True,
        "points": [
            series_point(START + timedelta(minutes=i), 25.5 + 0.02 * ((i % 5) - 2) / 2)
            for i in range(count)
        ],
    }
    result["point_ac001_state"] = {
        "unit": None,
        "aggregation": "raw",
        "sampling_interval_seconds": 60,
        "complete": True,
        "points": [series_point(START + timedelta(minutes=i), 1.0) for i in range(count)],
    }
    result["point_chiller_supply_temp"] = {
        "unit": "℃",
        "aggregation": "raw",
        "sampling_interval_seconds": 60,
        "complete": True,
        "points": [
            series_point(START + timedelta(minutes=i), 7.0 + 0.03 * ((i % 5) - 2) / 2)
            for i in range(count)
        ],
    }
    result["point_power_state"] = {
        "unit": None,
        "aggregation": "raw",
        "sampling_interval_seconds": 60,
        "complete": True,
        "points": [series_point(START + timedelta(minutes=i), 1.0) for i in range(count)],
    }
    return result


topology_relations = [
    {
        "source_system": "mock-topology",
        "query_asset_id": "asset_ac001",
        "rel_type": "supply_cooling",
        "direction": "upstream",
        "raw_rel_type": "chilled_water_supply",
        "from_device_id": "asset_chiller_01",
        "to_device_id": "asset_ac001",
        "relation_id": "rel_cool_ac001",
        "path_ref": "path_chiller_ac001",
        "temporal_basis": "current_registered_topology",
    },
    {
        "source_system": "mock-topology",
        "query_asset_id": "asset_ac002",
        "rel_type": "supply_cooling",
        "direction": "upstream",
        "raw_rel_type": "chilled_water_supply",
        "from_device_id": "asset_chiller_01",
        "to_device_id": "asset_ac002",
        "relation_id": "rel_cool_ac002",
        "path_ref": "path_chiller_ac002",
        "temporal_basis": "current_registered_topology",
    },
    {
        "source_system": "mock-topology",
        "query_asset_id": "asset_ac003",
        "rel_type": "supply_cooling",
        "direction": "upstream",
        "raw_rel_type": "chilled_water_supply",
        "from_device_id": "asset_chiller_01",
        "to_device_id": "asset_ac003",
        "relation_id": "rel_cool_ac003",
        "path_ref": "path_chiller_ac003",
        "temporal_basis": "current_registered_topology",
    },
    {
        "source_system": "mock-topology",
        "query_asset_id": "asset_ac001",
        "rel_type": "supply_power",
        "direction": "upstream",
        "raw_rel_type": "power_supply",
        "from_device_id": "asset_power_01",
        "to_device_id": "asset_ac001",
        "relation_id": "rel_power_ac001",
        "path_ref": "path_power_ac001",
        "temporal_basis": "current_registered_topology",
    },
    {
        "source_system": "mock-topology",
        "query_asset_id": "asset_ac002",
        "rel_type": "unknown",
        "direction": "upstream",
        "raw_rel_type": "control_link",
        "from_device_id": "asset_chiller_01",
        "to_device_id": "asset_ac002",
        "relation_id": "rel_unknown_ac002",
        "path_ref": "path_control_ac002",
        "temporal_basis": "current_registered_topology",
    },
]

work_orders = [
    {
        "work_order_id": "wo_001",
        "asset_id": "asset_ac001",
        "created_at": "2026-10-26T10:20:00+08:00",
        "status": "open",
        "summary": "值班人员已发起现场核查，尚未反馈结果",
    }
]

knowledge_docs = [
    {
        "doc_id": "sop_ac_high_temp",
        "doc_version": "2026.10",
        "doc_type": "sop",
        "title": "精密空调送风温度高核查规程",
        "device_types": ["precision_air_conditioner"],
        "site_scope": ["site_01"],
        "review_status": "approved",
        "publication_status": "published",
        "valid_from": "2026-01-01T00:00:00+08:00",
        "valid_to": "2027-12-31T23:59:59+08:00",
        "chunks": [
            {
                "chunk_id": "sop_ac_high_temp#1",
                "section": "初步核查",
                "text": "确认送风温度测点绑定和单位，核对回风温度、风机运行状态、运行模式与设定值；查看同一区域其他空调是否同步升温；检查冷冻水供水温度、阀门和支路状态。",
            },
            {
                "chunk_id": "sop_ac_high_temp#2",
                "section": "证据要求",
                "text": "持续超限判断需要有效阈值、连续采样、持续时间参数和足够历史覆盖；缺少参数或数据时只能记录未知，不得判定设备正常。",
            },
            {
                "chunk_id": "sop_ac_high_temp#3",
                "section": "安全边界",
                "text": "涉及停机、倒闸、投退、启停或设定值调整时，应按现场审批制度和操作票执行；本规程不替代现场操作许可。",
            },
        ],
    },
    {
        "doc_id": "manual_crac_x1",
        "doc_version": "2026.08",
        "doc_type": "manual",
        "title": "CRAC-X1 精密空调技术手册",
        "device_types": ["precision_air_conditioner"],
        "site_scope": [],
        "review_status": "approved",
        "publication_status": "published",
        "valid_from": "2026-01-01T00:00:00+08:00",
        "valid_to": "2028-12-31T23:59:59+08:00",
        "chunks": [
            {
                "chunk_id": "manual_crac_x1#1",
                "section": "运行参数",
                "text": "CRAC-X1 的送风温度控制与回风温度、设定值、风机反馈和冷冻水阀开度相关。现场核查应先确认测点通道与机组运行模式，再检查风机和冷冻水支路。",
            },
            {
                "chunk_id": "manual_crac_x1#2",
                "section": "维护检查",
                "text": "维护检查包括滤网压差、风机反馈、冷凝水排放和传感器接线。涉及部件更换或停机操作时，按现场规程和审批要求执行。",
            },
        ],
    },
]

faq_entries = [
    {
        "faq_id": "faq_crac_inspection",
        "canonical_question": "精密空调巡检项有哪些",
        "question_variants": ["精密空调日常巡检项目", "空调巡检要检查什么"],
        "answer": "通用巡检应核对送风/回风温度测点、运行状态、风机反馈、滤网压差、冷凝水排放和冷冻水支路；具体项目以现场有效 SOP 和型号手册为准。",
        "source_refs": [
            {"doc_id": "manual_crac_x1", "doc_version": "2026.08", "chunk_id": "manual_crac_x1#2"},
            {"doc_id": "sop_ac_high_temp", "doc_version": "2026.10", "chunk_id": "sop_ac_high_temp#1"},
        ],
        "scope": "generic",
        "risk_level": "low",
        "review_status": "approved",
        "publication_status": "published",
        "valid_from": "2026-01-01T00:00:00+08:00",
        "valid_to": "2027-12-31T23:59:59+08:00",
        "source_revision_hash": "demo-faq-rev-1",
    }
]

files = {
    "devices.json": devices,
    "point_bindings.json": point_bindings,
    "alarm_definitions.json": alarm_definitions,
    "alarm_episodes.json": alarm_episodes,
    "timeseries.json": build_series(),
    "topology_relations.json": topology_relations,
    "work_orders.json": work_orders,
    "knowledge_docs.json": knowledge_docs,
    "faq_entries.json": faq_entries,
}

for name, payload in files.items():
    (FIX / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

print(f"wrote {len(files)} fixture files to {FIX}")
