from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class Settings:
    app_name: str = "数据中心基础设施智能运维助手"
    short_name: str = "运维助手"
    business_tz: ZoneInfo = BUSINESS_TZ
    config_dir: Path = BASE_DIR / "config"
    published_dir: Path = BASE_DIR / "published_configs"
    fixtures_dir: Path = BASE_DIR / "fixtures"
    frontend_dir: Path = BASE_DIR / "frontend"

    # 演示用固定身份。生产环境应从认证依赖注入，不能由模型或请求体扩大范围。
    demo_user_id: str = "user_demo"
    demo_tenant_id: str = "tenant_demo"
    demo_platform_instance_id: str = "mock-platform-01"
    demo_site_ids: tuple[str, ...] = ("site_01",)

    # 固定模拟数据锚点。生产环境置空 DEMO_FIXED_NOW 后使用服务器时钟；
    # 页面和报告会明确展示本次 fixed_query_time，不能把模拟数据当作实时数据。
    demo_fixed_now: str | None = field(
        default_factory=lambda: os.getenv("DEMO_FIXED_NOW", "2026-10-26T10:30:00+08:00") or None
    )

    # 演示配置：分析窗口必须有明确来源，不能把“现在”当成完整分析窗口。
    analysis_window_seconds: int = 7200
    task_timeout_seconds: float = 8.0
    max_parallel_tasks: int = 4
    max_semantic_tasks: int = 4
    max_relation_edges: int = 6
    sse_heartbeat_seconds: float = 15.0

    # 全局硬限制：配置包只能更严，不能更宽。
    hard_max_step_count: int = 12
    hard_max_related_assets: int = 4
    hard_max_asset_hops: int = 1
    hard_max_raw_hops: int = 4
    hard_max_window_seconds: int = 7200
    hard_max_tool_calls: int = 32
    hard_deadline_ms: int = 15000

    llm_base_url: str | None = field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    llm_api_key: str | None = field(default_factory=lambda: os.getenv("LLM_API_KEY") or None)
    llm_model: str | None = field(default_factory=lambda: os.getenv("LLM_MODEL") or None)
    llm_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


settings = Settings()
