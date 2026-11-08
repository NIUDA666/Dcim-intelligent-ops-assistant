from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from app.scenarios.config_publisher import ConfigPublishError, publish_bundle

BASE_DIR = Path(__file__).resolve().parents[1]
PUBLISHED_DIR = BASE_DIR / "published_configs"
CURRENT_VERSION = "demo-2026-10-26.3"


@contextmanager
def _workspace():
    """项目内临时目录；Windows 的 pytest tmp_path 在本机受权限限制。"""
    root = BASE_DIR / ".publish_tmp"
    root.mkdir(exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="pytest-publish-", dir=root))
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_current_published_bundle_matches_index_and_manifest():
    index = json.loads((PUBLISHED_DIR / "index.json").read_text(encoding="utf-8"))
    assert index["current"] == CURRENT_VERSION
    assert [item["bundle_version"] for item in index["bundles"]] == [
        "demo-2026-10-26.1",
        "demo-2026-10-26.2",
        "demo-2026-10-26.3",
    ]
    for entry in index["bundles"]:
        assert (PUBLISHED_DIR / entry["bundle_version"] / "bundle.json").is_file()
        assert (PUBLISHED_DIR / entry["bundle_version"] / "manifest.json").is_file()

    bundle = json.loads(
        (PUBLISHED_DIR / CURRENT_VERSION / "bundle.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (PUBLISHED_DIR / CURRENT_VERSION / "manifest.json").read_text(encoding="utf-8")
    )
    assert bundle["bundle_version"] == CURRENT_VERSION
    assert bundle["manifest"] == manifest
    assert manifest["bundle_version"] == CURRENT_VERSION
    assert manifest["published_at"].endswith("+08:00")

    scenario_ids = {item["id"] for item in bundle["scenarios"]}
    assert scenario_ids == {"ac_supply_temperature_high", "ac_supply_temperature_high_dx"}
    assert bundle["fallback"]["id"] == "self_only"
    assert set(manifest["scenario_plan_hashes"]) == scenario_ids
    assert len(manifest["fallback_plan_hash"]) == 64
    assert all(len(value) == 64 for value in manifest["scenario_plan_hashes"].values())

    current = index["bundles"][-1]
    assert current["scenario_count"] == len(bundle["scenarios"])
    assert current["source_hashes"] == manifest["source_hashes"]


def test_publish_is_idempotent_and_versions_are_immutable():
    with _workspace() as workspace:
        config_dir = workspace / "config"
        published_dir = workspace / "published_configs"
        shutil.copytree(BASE_DIR / "config", config_dir)

        bundle, created = publish_bundle(config_dir, published_dir)
        assert created is True
        assert bundle.bundle_version == CURRENT_VERSION

        same, created_again = publish_bundle(config_dir, published_dir)
        assert created_again is False
        assert same.bundle_version == bundle.bundle_version

        bundle_path = published_dir / CURRENT_VERSION / "bundle.json"
        payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        payload["scenarios"][0]["version"] = "999"
        bundle_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        with pytest.raises(ConfigPublishError) as excinfo:
            publish_bundle(config_dir, published_dir)
        assert excinfo.value.code == "bundle_version_immutable"


def test_version_bump_publishes_new_bundle_without_touching_old():
    with _workspace() as workspace:
        config_dir = workspace / "config"
        published_dir = workspace / "published_configs"
        shutil.copytree(BASE_DIR / "config", config_dir)

        first, _ = publish_bundle(config_dir, published_dir)
        assert first.bundle_version == CURRENT_VERSION
        old_bundle = (
            published_dir / CURRENT_VERSION / "bundle.json"
        ).read_text(encoding="utf-8")

        for name in ("scenarios.yaml", "fallback.yaml", "scenario_snippets.yaml"):
            path = config_dir / name
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["bundle_version"] = "demo-2026-10-26.4"
            path.write_text(
                yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

        second, created = publish_bundle(config_dir, published_dir)
        assert created is True
        assert second.bundle_version == "demo-2026-10-26.4"
        assert (published_dir / CURRENT_VERSION / "bundle.json").read_text(
            encoding="utf-8"
        ) == old_bundle
        index = json.loads((published_dir / "index.json").read_text(encoding="utf-8"))
        assert index["current"] == "demo-2026-10-26.4"
        assert {item["bundle_version"] for item in index["bundles"]} == {
            CURRENT_VERSION,
            "demo-2026-10-26.4",
        }
