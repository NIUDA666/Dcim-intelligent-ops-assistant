from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from app.scenarios.config_publisher import ConfigPublishError, publish_bundle  # noqa: E402
from app.settings import settings  # noqa: E402


def main() -> int:
    try:
        bundle, created = publish_bundle(BASE_DIR / "config", settings.published_dir)
    except ConfigPublishError as exc:
        print(f"[publish failed] {exc.code}: {exc}")
        for detail in exc.details:
            print(f"  - {detail}")
        return 1

    action = "published" if created else "already published (idempotent)"
    print(f"[{action}] bundle={bundle.bundle_version}")
    print(f"  scenarios: {', '.join(item.id for item in bundle.scenarios)}")
    print(f"  fallback: {bundle.fallback.id}")
    print(f"  unreferenced snippets: {', '.join(bundle.manifest.unreferenced_snippets) or 'none'}")
    for warning in bundle.manifest.warnings:
        print(f"  [warning] {warning}")
    print("  plan hashes:")
    for scenario_id, plan_hash in bundle.manifest.scenario_plan_hashes.items():
        print(f"    {scenario_id}: {plan_hash[:16]}")
    print(f"    {bundle.fallback.id}: {bundle.manifest.fallback_plan_hash[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
