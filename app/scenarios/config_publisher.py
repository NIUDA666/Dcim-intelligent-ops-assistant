from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.settings import BUSINESS_TZ
from app.scenarios.models import (
    AlarmDictionary,
    BundleManifest,
    EvidenceStep,
    ExpandedStep,
    Fallback,
    FallbackFile,
    FeatureProfile,
    FeatureProfilesFile,
    MetricDictionary,
    ParameterOverride,
    ParameterSet,
    ParameterSetsFile,
    PublishedBundle,
    PublishedFallback,
    PublishedScenario,
    Scenario,
    ScenariosFile,
    Snippet,
    SnippetsFile,
)

INCLUDE_PATTERN = re.compile(r"^([a-z][a-z0-9_]{0,63})@([0-9]+(?:\.[0-9]+)*)$")
SUPPORTED_APPLIES = {"model_code", "site_id", "cooling_mode", "device_type"}


class ConfigPublishError(ValueError):
    def __init__(self, code: str, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigPublishError("yaml_parse_failed", f"{path.name}: {exc}") from exc


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigPublishError("json_parse_failed", f"{path.name}: {exc}") from exc


@dataclass(frozen=True)
class ConfigSources:
    snippets_file: SnippetsFile
    scenarios_file: ScenariosFile
    fallback_file: FallbackFile
    metric_dictionary: MetricDictionary
    alarm_dictionary: AlarmDictionary
    feature_profiles: FeatureProfilesFile
    parameter_sets: ParameterSetsFile
    source_hashes: dict[str, str]
    raw_snippets: dict[str, Any]


def load_sources(config_dir: Path) -> ConfigSources:
    paths = {
        "scenario_snippets.yaml": config_dir / "scenario_snippets.yaml",
        "scenarios.yaml": config_dir / "scenarios.yaml",
        "fallback.yaml": config_dir / "fallback.yaml",
        "metric_dictionary.json": config_dir / "metric_dictionary.json",
        "alarm_dictionary.json": config_dir / "alarm_dictionary.json",
        "feature_profiles.json": config_dir / "feature_profiles.json",
        "parameter_sets.json": config_dir / "parameter_sets.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ConfigPublishError("config_source_missing", "配置文件缺失", missing)

    raw = {name: safe_load_yaml(path) if path.suffix == ".yaml" else load_json(path) for name, path in paths.items()}

    try:
        snippets_file = SnippetsFile.model_validate(raw["scenario_snippets.yaml"])
        scenarios_file = ScenariosFile.model_validate(raw["scenarios.yaml"])
        fallback_file = FallbackFile.model_validate(raw["fallback.yaml"])
        metric_dictionary = MetricDictionary.model_validate(raw["metric_dictionary.json"])
        alarm_dictionary = AlarmDictionary.model_validate(raw["alarm_dictionary.json"])
        feature_profiles = FeatureProfilesFile.model_validate(raw["feature_profiles.json"])
        parameter_sets = ParameterSetsFile.model_validate(raw["parameter_sets.json"])
    except Exception as exc:
        raise ConfigPublishError("schema_validation_failed", f"配置 Schema 校验失败: {exc}") from exc

    versions = {
        snippets_file.bundle_version,
        scenarios_file.bundle_version,
        fallback_file.bundle_version,
    }
    if len(versions) != 1:
        raise ConfigPublishError("bundle_version_mismatch", f"各 YAML 文件 bundle_version 不一致: {sorted(versions)}")

    return ConfigSources(
        snippets_file=snippets_file,
        scenarios_file=scenarios_file,
        fallback_file=fallback_file,
        metric_dictionary=metric_dictionary,
        alarm_dictionary=alarm_dictionary,
        feature_profiles=feature_profiles,
        parameter_sets=parameter_sets,
        source_hashes={name: file_hash(path) for name, path in paths.items()},
        raw_snippets=raw["scenario_snippets.yaml"].get("snippets", {}),
    )


def _metric_known(metric_dictionary: MetricDictionary, code: str) -> bool:
    return code == "$trigger_metric" or code in metric_dictionary.metrics


def _metric_applies(metric_dictionary: MetricDictionary, code: str, device_type: str) -> bool:
    if code == "$trigger_metric":
        return True
    spec = metric_dictionary.metrics.get(code)
    return bool(spec and device_type in spec.device_types)


def _validate_metrics_of_step(
    step: EvidenceStep,
    dictionary: MetricDictionary,
    *,
    owner: str,
    target_device_type: str | None = None,
    asset_types: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if step.metrics is None:
        return errors
    codes = list(step.metrics.required) + list(step.metrics.optional)
    for code in codes:
        if not _metric_known(dictionary, code):
            errors.append(f"{owner}: 未注册指标 {code}")
            continue
        if code == "$trigger_metric" and not owner.startswith("fallback"):
            errors.append(f"{owner}: $trigger_metric 仅允许在兜底计划中出现")
        if target_device_type and not _metric_applies(dictionary, code, target_device_type):
            errors.append(f"{owner}: 指标 {code} 不适用于设备类型 {target_device_type}")
    if asset_types:
        for code in step.metrics.required:
            if code != "$trigger_metric" and not any(_metric_applies(dictionary, code, t) for t in asset_types):
                errors.append(f"{owner}: 指标 {code} 不适用于任何目标资产类型 {asset_types}")
    return errors


def _validate_snippet_metrics(source: ConfigSources) -> list[str]:
    errors: list[str] = []
    dictionary = source.metric_dictionary
    for snippet_id, snippet in source.snippets_file.snippets.items():
        for step in snippet.steps:
            errors.extend(_validate_metrics_of_step(step, dictionary, owner=f"snippet {snippet_id}/{step.id}"))
    return errors


def _expand_includes(scenario: Scenario, snippets: dict[str, Snippet]) -> tuple[list[ExpandedStep], dict[str, str], list[str]]:
    errors: list[str] = []
    steps: list[ExpandedStep] = []
    versions: dict[str, str] = {}
    seen_ids: set[str] = set()
    for include in scenario.evidence_plan.include:
        match = INCLUDE_PATTERN.match(include)
        if not match:
            errors.append(f"scenario {scenario.id}: include 格式非法 {include!r}，应为 snippet_id@version")
            continue
        snippet_id, version = match.group(1), match.group(2)
        snippet = snippets.get(snippet_id)
        if snippet is None:
            errors.append(f"scenario {scenario.id}: 未知 include {include}")
            continue
        if snippet.version != version:
            errors.append(f"scenario {scenario.id}: include {include} 与已发布片段版本 {snippet.version} 不一致")
            continue
        if snippet_id in versions:
            errors.append(f"scenario {scenario.id}: 片段 {snippet_id} 重复引用")
            continue
        versions[snippet_id] = version
        for step in snippet.steps:
            uid = f"{snippet_id}@{version}/{step.id}"
            if uid in seen_ids:
                errors.append(f"scenario {scenario.id}: 展开步骤标识重复 {uid}")
                continue
            seen_ids.add(uid)
            steps.append(
                ExpandedStep(
                    **step.model_dump(),
                    step_uid=uid,
                    source_snippet=snippet_id,
                    source_snippet_version=version,
                )
            )
    return steps, versions, errors


def _match_scope_overlap(left: Scenario, right: Scenario) -> bool:
    keys = ("device_type", "metric_code", "anomaly_kind")
    left_match = left.match
    right_match = right.match
    for key in keys:
        if getattr(left_match, key) != getattr(right_match, key):
            return False
    for key in ("cooling_mode", "model_code", "site_id"):
        a, b = getattr(left_match, key), getattr(right_match, key)
        if a is not None and b is not None and a != b:
            return False
    return True


def _validate_scenario_conflicts(scenarios: list[Scenario]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for i, left in enumerate(scenarios):
        for right in scenarios[i + 1 :]:
            if not _match_scope_overlap(left, right):
                continue
            if left.priority == right.priority:
                errors.append(
                    f"场景 {left.id} 与 {right.id} 同优先级 {left.priority} 且匹配范围重叠，拒绝发布"
                )
            else:
                warnings.append(
                    f"场景 {left.id} 与 {right.id} 匹配范围重叠，按优先级选择；低优先级场景可能不可达"
                )
    return errors, warnings


def _validate_profile_metrics(
    source: ConfigSources,
    profile: FeatureProfile,
    *,
    owner: str,
    device_type: str | None = None,
) -> list[str]:
    errors: list[str] = []
    for metric_code in list(profile.required_metrics) + list(profile.optional_metrics):
        if metric_code not in source.metric_dictionary.metrics:
            errors.append(f"{owner}: 特征 profile {profile.version} 引用未注册指标 {metric_code}")
        elif device_type and not _metric_applies(source.metric_dictionary, metric_code, device_type):
            errors.append(f"{owner}: 指标 {metric_code} 不适用于设备类型 {device_type}")
    return errors


def _validate_parameter_values(
    values: dict[str, Any],
    profile: FeatureProfile,
    owner: str,
    *,
    require_window: bool = True,
) -> list[str]:
    errors: list[str] = []
    for key, value in values.items():
        entry = profile.parameter_schema.get(key)
        if entry is None:
            errors.append(f"{owner}: 参数 {key} 未在 profile 参数 Schema 中声明")
            continue
        if entry.type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(f"{owner}: 参数 {key} 必须为整数")
                continue
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{owner}: 参数 {key} 必须为数值")
            continue
        if entry.minimum is not None and value < entry.minimum:
            errors.append(f"{owner}: 参数 {key} 小于最小值 {entry.minimum}")
        if entry.maximum is not None and value > entry.maximum:
            errors.append(f"{owner}: 参数 {key} 大于最大值 {entry.maximum}")
    if "window_seconds" not in values and require_window:
        errors.append(f"{owner}: 缺少必要执行参数 window_seconds")
    return errors


def _validate_parameter_sets(source: ConfigSources) -> list[str]:
    errors: list[str] = []
    profiles = source.feature_profiles.profiles
    sets = source.parameter_sets
    common = sets.common_set
    profile = profiles.get(common.profile_id)
    if profile is None:
        errors.append(f"公共参数集引用未知 profile {common.profile_id}")
    else:
        errors.extend(_validate_parameter_values(common.values, profile, "common_set"))
        for key in profile.parameter_schema:
            if key not in common.values:
                errors.append(f"common_set: profile {common.profile_id} 缺少参数 {key}")
    fallback_profile = profiles.get(sets.fallback_set.profile_id)
    if fallback_profile is None:
        errors.append(f"兜底参数集引用未知 profile {sets.fallback_set.profile_id}")
    else:
        errors.extend(_validate_parameter_values(sets.fallback_set.values, fallback_profile, "fallback_set"))
        for key in fallback_profile.parameter_schema:
            if key not in sets.fallback_set.values:
                errors.append(f"fallback_set: profile {sets.fallback_set.profile_id} 缺少参数 {key}")

    for override in sets.overrides:
        owner = f"override {override.parameter_set_id}"
        override_profile = profiles.get(override.profile_id)
        if override_profile is None:
            errors.append(f"{owner}: 引用未知 profile {override.profile_id}")
            continue
        unknown = set(override.applies) - SUPPORTED_APPLIES
        if unknown:
            errors.append(f"{owner}: 不支持的适用条件 {sorted(unknown)}")
        if any(not values for values in override.applies.values()):
            errors.append(f"{owner}: 适用条件不能为空列表")
        common_profile = profiles.get(common.profile_id)
        if common_profile and override.profile_id != common.profile_id:
            errors.append(f"{owner}: 覆盖集 profile 与公共参数集不一致")
            continue
        errors.extend(_validate_parameter_values(override.values, override_profile, owner, require_window=False))
        for key in override.values:
            entry = override_profile.parameter_schema[key]
            if not entry.overridable:
                errors.append(f"{owner}: 参数 {key} 不允许覆盖")

    for i, left in enumerate(sets.overrides):
        for right in sets.overrides[i + 1 :]:
            if left.profile_id != right.profile_id:
                continue
            if _override_scope_overlap(left, right):
                errors.append(
                    f"覆盖集 {left.parameter_set_id} 与 {right.parameter_set_id} 可同时适用于同一设备，拒绝发布"
                )
    return errors


def _override_scope_overlap(left: ParameterOverride, right: ParameterOverride) -> bool:
    for key in set(left.applies) | set(right.applies):
        left_values = set(left.applies.get(key, []))
        right_values = set(right.applies.get(key, []))
        if left_values and right_values and not (left_values & right_values):
            return False
    return True


def build_bundle(source: ConfigSources) -> PublishedBundle:
    errors: list[str] = []
    warnings: list[str] = []

    metric_dictionary = source.metric_dictionary
    for alarm_type_id, alarm in source.alarm_dictionary.alarm_types.items():
        if alarm.metric_code not in metric_dictionary.metrics:
            errors.append(f"alarm_dictionary: 告警类型 {alarm_type_id} 引用未注册指标 {alarm.metric_code}")
    for alias, mapping in source.alarm_dictionary.alias_index.items():
        if not isinstance(mapping, dict):
            errors.append(f"alarm_dictionary: 别名 {alias} 的映射必须为对象")
            continue
        metric_code = mapping.get("metric_code")
        anomaly_kind = mapping.get("anomaly_kind")
        if metric_code not in metric_dictionary.metrics:
            errors.append(f"alarm_dictionary: 别名 {alias} 引用未注册指标 {metric_code}")
        if anomaly_kind not in {"high", "low", "missing", "communication"}:
            errors.append(f"alarm_dictionary: 别名 {alias} 的 anomaly_kind 非法")

    errors.extend(_validate_snippet_metrics(source))

    snippets = source.snippets_file.snippets
    referenced: set[str] = set()
    published_scenarios: list[PublishedScenario] = []
    scenario_hashes: dict[str, str] = {}
    dependencies: dict[str, list[str]] = {}

    for scenario in source.scenarios_file.scenarios:
        owner = f"scenario {scenario.id}"
        metric_spec = metric_dictionary.metrics.get(scenario.match.metric_code)
        if metric_spec is None:
            errors.append(f"{owner}: 匹配字段引用未注册指标 {scenario.match.metric_code}")
        elif scenario.match.device_type not in metric_spec.device_types:
            errors.append(f"{owner}: 指标 {scenario.match.metric_code} 不适用于设备类型 {scenario.match.device_type}")
        profile = source.feature_profiles.profiles.get(scenario.evidence_plan.feature_profile)
        if profile is None:
            errors.append(f"{owner}: 引用未知 feature_profile {scenario.evidence_plan.feature_profile}")
            continue
        errors.extend(
            _validate_profile_metrics(source, profile, owner=owner, device_type=scenario.match.device_type)
        )
        expanded, snippet_versions, expand_errors = _expand_includes(scenario, snippets)
        errors.extend(expand_errors)
        if len(expanded) > source.snippets_file.limits.max_step_count:
            errors.append(
                f"{owner}: 展开步骤 {len(expanded)} 超过 max_step_count={source.snippets_file.limits.max_step_count}"
            )
        for step in expanded:
            errors.extend(
                _validate_metrics_of_step(
                    step,
                    metric_dictionary,
                    owner=f"{owner}/{step.step_uid}",
                    target_device_type=scenario.match.device_type if step.action in {"metric_history", "peer_evidence"} else None,
                    asset_types=step.asset_types,
                )
            )
        covered = {
            metric
            for step in expanded
            if step.metrics
            for metric in step.metrics.required + step.metrics.optional
        }
        for required_metric in profile.required_metrics:
            if required_metric not in covered:
                errors.append(f"{owner}: 计划未覆盖 profile 必需指标 {required_metric}")
        for key in snippet_versions:
            referenced.add(key)
        dependencies[scenario.id] = sorted(snippet_versions) + [profile.version]
        base_plan = {
            "scenario_id": scenario.id,
            "scenario_version": scenario.version,
            "steps": [step.model_dump() for step in expanded],
            "feature_profile": scenario.evidence_plan.feature_profile,
            "feature_profile_version": profile.version,
        }
        scenario_hashes[scenario.id] = stable_hash(base_plan)
        published_scenarios.append(
            PublishedScenario(
                id=scenario.id,
                version=scenario.version,
                priority=scenario.priority,
                match=scenario.match,
                feature_profile=scenario.evidence_plan.feature_profile,
                profile_version=profile.version,
                include=scenario.evidence_plan.include,
                snippet_versions=snippet_versions,
                expanded_steps=expanded,
                base_plan_hash=scenario_hashes[scenario.id],
            )
        )

    conflict_errors, conflict_warnings = _validate_scenario_conflicts(source.scenarios_file.scenarios)
    errors.extend(conflict_errors)
    warnings.extend(conflict_warnings)
    errors.extend(_validate_parameter_sets(source))

    fallback = source.fallback_file.fallback
    fallback_source = source.feature_profiles.profiles.get(fallback.evidence_plan.feature_profile)
    if fallback_source is None:
        errors.append(f"fallback: 引用未知 feature_profile {fallback.evidence_plan.feature_profile}")
    else:
        errors.extend(
            _validate_profile_metrics(source, fallback_source, owner="fallback")
        )
    errors.extend(_validate_fallback_steps(source, fallback))

    if errors:
        raise ConfigPublishError("config_validation_failed", "配置校验失败", errors)

    fallback_steps = [
        ExpandedStep(
            **step.model_dump(),
            step_uid=f"fallback@{fallback.version}/{step.id}",
            source_snippet=None,
            source_snippet_version=None,
        )
        for step in fallback.evidence_plan.steps
    ]
    if len(fallback_steps) > source.snippets_file.limits.max_step_count:
        raise ConfigPublishError(
            "config_validation_failed",
            "配置校验失败",
            [f"fallback 展开步骤超过 max_step_count={source.snippets_file.limits.max_step_count}"],
        )
    fallback_plan_hash = stable_hash(
        {
            "fallback_id": fallback.id,
            "fallback_version": fallback.version,
            "steps": [step.model_dump() for step in fallback_steps],
            "feature_profile": fallback.evidence_plan.feature_profile,
            "feature_profile_version": fallback_source.version,
        }
    )
    published_fallback = PublishedFallback(
        id=fallback.id,
        version=fallback.version,
        feature_profile=fallback.evidence_plan.feature_profile,
        profile_version=fallback_source.version,
        expanded_steps=fallback_steps,
        base_plan_hash=fallback_plan_hash,
    )

    if len(source.snippets_file.snippets) - len(referenced) > 0:
        unreferenced = sorted(set(source.snippets_file.snippets) - referenced)
        warnings.append(f"存在未被场景引用的片段：{', '.join(unreferenced)}")
    else:
        unreferenced = []

    published_at = datetime.now(tz=BUSINESS_TZ).isoformat(timespec="seconds")
    manifest = BundleManifest(
        bundle_version=source.snippets_file.bundle_version,
        published_at=published_at,
        source_hashes=source.source_hashes,
        scenario_plan_hashes=scenario_hashes,
        fallback_plan_hash=fallback_plan_hash,
        dependencies=dependencies,
        unreferenced_snippets=unreferenced,
        warnings=warnings,
    )
    return PublishedBundle(
        schema_version="published-bundle/1",
        bundle_version=source.snippets_file.bundle_version,
        limits=source.snippets_file.limits,
        metric_dictionary=metric_dictionary,
        alarm_dictionary=source.alarm_dictionary,
        feature_profiles=source.feature_profiles,
        parameter_sets=source.parameter_sets,
        scenarios=published_scenarios,
        fallback=published_fallback,
        manifest=manifest,
    )


def _validate_fallback_steps(source: ConfigSources, fallback: Fallback) -> list[str]:
    errors: list[str] = []
    trigger_count = 0
    for index, step in enumerate(fallback.evidence_plan.steps):
        owner = f"fallback/{step.id}"
        if step.action not in {"metric_history", "alarm_history"}:
            errors.append(f"{owner}: 兜底只允许本机指标与告警动作，不能查询上下游或同机房对照")
        errors.extend(_validate_metrics_of_step(step, source.metric_dictionary, owner=owner))
        if step.metrics:
            for metric in step.metrics.required:
                if metric == "$trigger_metric":
                    trigger_count += 1
                    if index != 0:
                        errors.append(f"{owner}: $trigger_metric 必须出现在兜底首个指标步骤")
            for metric in step.metrics.optional:
                if metric == "$trigger_metric":
                    errors.append(f"{owner}: $trigger_metric 不能作为可选指标")
    if trigger_count != 1:
        errors.append("fallback: 必须且只能出现一次 $trigger_metric 占位符")
    return errors


def publish_bundle(config_dir: Path, published_dir: Path) -> tuple[PublishedBundle, bool]:
    source = load_sources(config_dir)
    bundle = build_bundle(source)
    target_dir = published_dir / bundle.bundle_version
    bundle_path = target_dir / "bundle.json"
    manifest_path = target_dir / "manifest.json"

    if bundle_path.exists():
        existing = json.loads(bundle_path.read_text(encoding="utf-8"))
        existing.pop("manifest", None)
        candidate = bundle.model_dump()
        candidate.pop("manifest", None)
        if existing != candidate:
            raise ConfigPublishError(
                "bundle_version_immutable",
                f"配置包 {bundle.bundle_version} 已存在且内容不同；请提升 bundle_version 后重新发布",
            )
        return PublishedBundle.model_validate(json.loads(bundle_path.read_text(encoding="utf-8"))), False

    target_dir.mkdir(parents=True, exist_ok=True)
    payload = bundle.model_dump()
    _atomic_write(bundle_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    _atomic_write(manifest_path, json.dumps(bundle.manifest.model_dump(), ensure_ascii=False, indent=2, sort_keys=True))
    _update_index(published_dir, bundle)
    return bundle, True


def _update_index(published_dir: Path, bundle: PublishedBundle) -> None:
    index_path = published_dir / "index.json"
    existing: dict[str, Any] = {"current": bundle.bundle_version, "bundles": []}
    if index_path.exists():
        existing = json.loads(index_path.read_text(encoding="utf-8"))
        existing.setdefault("bundles", [])
    entries = {entry["bundle_version"]: entry for entry in existing["bundles"] if isinstance(entry, dict)}
    entries[bundle.bundle_version] = {
        "bundle_version": bundle.bundle_version,
        "published_at": bundle.manifest.published_at,
        "scenario_count": len(bundle.scenarios),
        "source_hashes": bundle.manifest.source_hashes,
    }
    existing["current"] = bundle.bundle_version
    existing["bundles"] = [entries[key] for key in sorted(entries)]
    _atomic_write(index_path, json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True))


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
