from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.scenarios.config_publisher import stable_hash
from app.scenarios.models import (
    ExpandedStep,
    FeatureProfile,
    ParameterOverride,
    PublishedBundle,
    PublishedFallback,
    PublishedScenario,
)


class ConfigUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PlanResolutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParameterResolution:
    profile_id: str
    profile_version: str
    values: dict[str, Any]
    parameter_set_ids: list[str]
    sources: list[str]
    override_basis: dict[str, Any] | None


@dataclass(frozen=True)
class PlanSelection:
    scenario: PublishedScenario | None
    fallback: PublishedFallback | None
    fallback_used: bool
    match_basis: str
    match_anomaly_kind: str
    parameters: ParameterResolution
    steps: list[ExpandedStep]
    plan_hash: str

    @property
    def scenario_id(self) -> str:
        return self.scenario.id if self.scenario else self.fallback.id  # type: ignore[union-attr]

    @property
    def scenario_version(self) -> str:
        return self.scenario.version if self.scenario else self.fallback.version  # type: ignore[union-attr]

    @property
    def feature_profile(self) -> str:
        return self.scenario.feature_profile if self.scenario else self.fallback.feature_profile  # type: ignore[union-attr]

    @property
    def profile_version(self) -> str:
        return self.parameters.profile_version

    @property
    def snippet_versions(self) -> dict[str, str]:
        return dict(self.scenario.snippet_versions) if self.scenario else {}


def runtime_metric_dictionary(bundle: PublishedBundle) -> dict[str, dict[str, Any]]:
    """把已发布配置转成运行期只读字典，解析层不直接依赖 Pydantic 模型。"""
    return {code: spec.model_dump() for code, spec in bundle.metric_dictionary.metrics.items()}


def runtime_alarm_dictionary(bundle: PublishedBundle) -> dict[str, Any]:
    return bundle.alarm_dictionary.model_dump()


class ConfigStore:
    def __init__(self, published_dir: Path):
        self.published_dir = published_dir
        self._cache: dict[str, PublishedBundle] = {}

    def current_version(self) -> str:
        index_path = self.published_dir / "index.json"
        if not index_path.is_file():
            raise ConfigUnavailable(
                "no_published_config",
                "没有已发布配置包，请先运行 python scripts/publish_configs.py",
            )
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigUnavailable("config_index_invalid", f"配置索引无法解析: {exc}") from exc
        version = index.get("current")
        if not version or not (self.published_dir / version / "bundle.json").is_file():
            raise ConfigUnavailable("config_bundle_missing", "配置索引指向的发布包不存在")
        return version

    def load(self, version: str | None = None) -> PublishedBundle:
        resolved = version or self.current_version()
        cached = self._cache.get(resolved)
        if cached is not None:
            return cached
        path = self.published_dir / resolved / "bundle.json"
        if not path.is_file():
            raise ConfigUnavailable("config_bundle_missing", f"配置包 {resolved} 不存在")
        try:
            bundle = PublishedBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise ConfigUnavailable("config_bundle_invalid", f"配置包 {resolved} 校验失败: {exc}") from exc
        if bundle.bundle_version != resolved:
            raise ConfigUnavailable("config_bundle_invalid", "配置包版本与目录不一致")
        self._cache[resolved] = bundle
        return bundle


def _device_value(device: dict[str, Any], key: str) -> str | None:
    value = device.get(key)
    return value if isinstance(value, str) else None


def _override_matches(override: ParameterOverride, device: dict[str, Any]) -> bool:
    for key, allowed in override.applies.items():
        actual = _device_value(device, key)
        if actual is None or actual not in allowed:
            return False
    return True


def _validate_runtime_values(values: dict[str, Any], profile: FeatureProfile) -> None:
    for key, value in values.items():
        entry = profile.parameter_schema.get(key)
        if entry is None:
            raise PlanResolutionError("parameter_not_declared", f"参数 {key} 未在 profile 中声明")
        if entry.type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise PlanResolutionError("parameter_type_invalid", f"参数 {key} 必须为整数")
        if entry.type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise PlanResolutionError("parameter_type_invalid", f"参数 {key} 必须为数值")
        if entry.minimum is not None and value < entry.minimum:
            raise PlanResolutionError("parameter_out_of_range", f"参数 {key} 低于允许下限")
        if entry.maximum is not None and value > entry.maximum:
            raise PlanResolutionError("parameter_out_of_range", f"参数 {key} 高于允许上限")
    if "window_seconds" not in values:
        raise PlanResolutionError("parameter_missing", "缺少必要执行参数 window_seconds")


def _resolve_profile_parameters(
    bundle: PublishedBundle,
    profile: FeatureProfile,
    device: dict[str, Any] | None,
    *,
    use_fallback_set: bool,
) -> ParameterResolution:
    sets = bundle.parameter_sets
    if use_fallback_set:
        base = sets.fallback_set
        if bundle.feature_profiles.profiles.get(base.profile_id) is not profile:
            raise PlanResolutionError("parameter_profile_mismatch", "兜底参数集 profile 不匹配")
        values = dict(base.values)
        _validate_runtime_values(values, profile)
        return ParameterResolution(
            profile_id=base.profile_id,
            profile_version=profile.version,
            values=values,
            parameter_set_ids=[f"{base.parameter_set_id}@{base.version}"],
            sources=[base.source],
            override_basis=None,
        )

    base = sets.common_set
    if bundle.feature_profiles.profiles.get(base.profile_id) is not profile:
        raise PlanResolutionError("parameter_profile_mismatch", "公共参数集 profile 不匹配")
    values = dict(base.values)
    applied: list[ParameterOverride] = []
    if device is not None:
        applicable = [item for item in sets.overrides if item.profile_id == base.profile_id and _override_matches(item, device)]
        if len(applicable) > 1:
            names = ", ".join(item.parameter_set_id for item in applicable)
            raise PlanResolutionError("parameter_override_conflict", f"多份参数覆盖同时适用：{names}")
        applied = applicable
        for override in applied:
            values.update(override.values)
    _validate_runtime_values(values, profile)
    parameter_set_ids = [f"{base.parameter_set_id}@{base.version}"]
    sources = [base.source]
    override_basis: dict[str, Any] | None = None
    for override in applied:
        parameter_set_ids.append(f"{override.parameter_set_id}@{override.version}")
        sources.append(override.source)
        override_basis = override.applies
    return ParameterResolution(
        profile_id=base.profile_id,
        profile_version=profile.version,
        values=values,
        parameter_set_ids=parameter_set_ids,
        sources=sources,
        override_basis=override_basis,
    )


def select_plan(
    bundle: PublishedBundle,
    *,
    device: dict[str, Any] | None,
    metric_code: str,
    match_anomaly_kind: str,
    match_basis: str,
) -> PlanSelection:
    candidates: list[PublishedScenario] = []
    if device is not None:
        for scenario in bundle.scenarios:
            match = scenario.match
            if match.device_type != device.get("type_code"):
                continue
            if match.metric_code != metric_code or match.anomaly_kind != match_anomaly_kind:
                continue
            if match.cooling_mode and device.get("cooling_mode") != match.cooling_mode:
                continue
            if match.model_code and device.get("model_code") != match.model_code:
                continue
            if match.site_id and device.get("site_id") != match.site_id:
                continue
            candidates.append(scenario)

    if candidates:
        candidates.sort(key=lambda item: item.priority, reverse=True)
        if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
            names = ", ".join(item.id for item in candidates[:2])
            raise PlanResolutionError("scenario_conflict", f"场景优先级并列，拒绝任意选择：{names}")
        chosen = candidates[0]
        profile = bundle.feature_profiles.profiles.get(chosen.feature_profile)
        if profile is None:
            raise PlanResolutionError("feature_profile_missing", "场景引用的特征 profile 不存在")
        parameters = _resolve_profile_parameters(bundle, profile, device, use_fallback_set=False)
        steps = _bind_trigger_metric(chosen.expanded_steps, metric_code)
        plan_hash = _runtime_plan_hash(bundle, chosen, None, steps, parameters)
        return PlanSelection(
            scenario=chosen,
            fallback=None,
            fallback_used=False,
            match_basis=match_basis,
            match_anomaly_kind=match_anomaly_kind,
            parameters=parameters,
            steps=steps,
            plan_hash=plan_hash,
        )

    fallback = bundle.fallback
    profile = bundle.feature_profiles.profiles.get(fallback.feature_profile)
    if profile is None:
        raise PlanResolutionError("feature_profile_missing", "兜底引用的特征 profile 不存在")
    parameters = _resolve_profile_parameters(bundle, profile, device, use_fallback_set=True)
    steps = _bind_trigger_metric(fallback.expanded_steps, metric_code)
    plan_hash = _runtime_plan_hash(bundle, None, fallback, steps, parameters)
    return PlanSelection(
        scenario=None,
        fallback=fallback,
        fallback_used=True,
        match_basis=match_basis,
        match_anomaly_kind=match_anomaly_kind,
        parameters=parameters,
        steps=steps,
        plan_hash=plan_hash,
    )


def _bind_trigger_metric(steps: list[ExpandedStep], metric_code: str) -> list[ExpandedStep]:
    bound: list[ExpandedStep] = []
    for step in steps:
        if step.metrics and "$trigger_metric" in step.metrics.required:
            metrics = step.metrics.model_copy(
                update={
                    "required": [metric_code if item == "$trigger_metric" else item for item in step.metrics.required],
                    "optional": [metric_code if item == "$trigger_metric" else item for item in step.metrics.optional],
                }
            )
            step = step.model_copy(update={"metrics": metrics})
        bound.append(step)
    return bound


def _runtime_plan_hash(
    bundle: PublishedBundle,
    scenario: PublishedScenario | None,
    fallback: PublishedFallback | None,
    steps: list[ExpandedStep],
    parameters: ParameterResolution,
) -> str:
    identity = {
        "bundle_version": bundle.bundle_version,
        "scenario_id": scenario.id if scenario else fallback.id,  # type: ignore[union-attr]
        "scenario_version": scenario.version if scenario else fallback.version,  # type: ignore[union-attr]
        "base_plan_hash": scenario.base_plan_hash if scenario else fallback.base_plan_hash,  # type: ignore[union-attr]
        "steps": [step.model_dump() for step in steps],
        "feature_profile": parameters.profile_id,
        "feature_profile_version": parameters.profile_version,
        "resolved_parameters": parameters.values,
        "parameter_set_ids": parameters.parameter_set_ids,
    }
    return stable_hash(identity)
