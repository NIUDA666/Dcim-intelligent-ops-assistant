from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.settings import settings


@dataclass(frozen=True)
class AccessContext:
    user_id: str
    tenant_id: str
    platform_instance_id: str
    site_ids: frozenset[str]

    def allows_device(self, device: dict[str, Any]) -> bool:
        return (
            device.get("tenant_id") == self.tenant_id
            and device.get("platform_instance_id") == self.platform_instance_id
            and device.get("site_id") in self.site_ids
        )


def demo_access_context() -> AccessContext:
    return AccessContext(
        user_id=settings.demo_user_id,
        tenant_id=settings.demo_tenant_id,
        platform_instance_id=settings.demo_platform_instance_id,
        site_ids=frozenset(settings.demo_site_ids),
    )
