from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.settings import settings


AnomalyKind = Literal["high", "low", "missing", "communication"]


class MetricSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique(self) -> "MetricSelection":
        if len(set(self.required)) != len(self.required):
            raise ValueError("required_metrics_duplicated")
        if len(set(self.optional)) != len(self.optional):
            raise ValueError("optional_metrics_duplicated")
        overlap = set(self.required) & set(self.optional)
        if overlap:
            raise ValueError(f"metric_declared_twice:{sorted(overlap)}")
        return self


class EvidenceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    action: Literal["metric_history", "alarm_history", "related_evidence", "peer_evidence"]
    target: Literal["self"] | None = None
    metrics: MetricSelection | None = None
    rel_type: Literal["supply_cooling", "supply_power"] | None = None
    direction: Literal["upstream", "downstream"] | None = None
    asset_types: list[str] | None = None
    include_alarms: bool = False
    selector: Literal["same_room_same_device_type"] | None = None

    @model_validator(mode="after")
    def _check_action_shape(self) -> "EvidenceStep":
        if self.action == "metric_history":
            if self.target != "self" or self.metrics is None:
                raise ValueError("metric_history_requires_self_and_metrics")
            if self.rel_type or self.direction or self.asset_types or self.selector:
                raise ValueError("metric_history_has_inapplicable_fields")
        elif self.action == "alarm_history":
            if self.target != "self":
                raise ValueError("alarm_history_requires_self")
            if self.metrics or self.rel_type or self.direction or self.asset_types or self.selector:
                raise ValueError("alarm_history_has_inapplicable_fields")
        elif self.action == "related_evidence":
            if self.rel_type is None or self.direction is None or not self.asset_types:
                raise ValueError("related_evidence_requires_relation_and_asset_types")
            if self.metrics is None or not self.metrics.required:
                raise ValueError("related_evidence_requires_metrics")
            if self.target or self.selector:
                raise ValueError("related_evidence_has_inapplicable_fields")
        elif self.action == "peer_evidence":
            if self.selector is None or self.metrics is None or not self.metrics.required:
                raise ValueError("peer_evidence_requires_selector_and_metrics")
            if self.target or self.rel_type or self.direction or self.asset_types:
                raise ValueError("peer_evidence_has_inapplicable_fields")
        return self


class ExpandedStep(EvidenceStep):
    step_uid: str
    source_snippet: str | None = None
    source_snippet_version: str | None = None


class Snippet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=r"^[0-9]+(\.[0-9]+)*$")
    steps: list[EvidenceStep] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _check_step_ids(self) -> "Snippet":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("snippet_step_ids_duplicated")
        return self


class ScenarioMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_type: str = Field(min_length=1)
    cooling_mode: str | None = None
    model_code: str | None = None
    site_id: str | None = None
    metric_code: str
    anomaly_kind: AnomalyKind


class ScenarioPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include: list[str] = Field(min_length=1)
    feature_profile: str


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(pattern=r"^[0-9]+(\.[0-9]+)*$")
    priority: int = Field(ge=0, le=1000)
    match: ScenarioMatch
    evidence_plan: ScenarioPlan

    @model_validator(mode="after")
    def _check_includes(self) -> "Scenario":
        if len(self.evidence_plan.include) != len(set(self.evidence_plan.include)):
            raise ValueError("scenario_include_duplicated")
        return self


class FallbackPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[EvidenceStep] = Field(min_length=1, max_length=8)
    feature_profile: str


class Fallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(pattern=r"^[0-9]+(\.[0-9]+)*$")
    evidence_plan: FallbackPlan


class ExecutionLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_step_count: int = Field(ge=1, le=settings.hard_max_step_count)
    max_related_assets: int = Field(ge=1, le=settings.hard_max_related_assets)
    max_asset_hops: int = Field(ge=1, le=settings.hard_max_asset_hops)
    max_raw_hops: int = Field(ge=1, le=settings.hard_max_raw_hops)
    max_window_seconds: int = Field(ge=60, le=settings.hard_max_window_seconds)
    max_tool_calls: int = Field(ge=1, le=settings.hard_max_tool_calls)
    deadline_ms: int = Field(ge=1000, le=settings.hard_deadline_ms)


class SnippetsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evidence-config/1"]
    bundle_version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    limits: ExecutionLimits
    snippets: dict[str, Snippet]

    @model_validator(mode="after")
    def _check_snippet_count(self) -> "SnippetsFile":
        if not self.snippets:
            raise ValueError("snippets_empty")
        return self


class ScenariosFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evidence-config/1"]
    bundle_version: str
    scenarios: list[Scenario] = Field(min_length=1)


class FallbackFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["evidence-config/1"]
    bundle_version: str
    fallback: Fallback


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    unit: str | None
    value_type: Literal["float", "integer", "enum", "boolean"]
    device_types: list[str] = Field(min_length=1)
    aggregations: list[Literal["latest", "avg", "max", "min", "raw"]] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    quality_required: bool = True


class MetricDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["metric-dictionary/1"]
    dictionary_version: str
    metrics: dict[str, MetricSpec]


class AlarmTypeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    metric_code: str
    anomaly_kind: AnomalyKind
    lifecycle_mode: Literal["occurrence_recovery", "state_only"]
    version: str


class AlarmDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["alarm-dictionary/1"]
    dictionary_version: str
    alarm_types: dict[str, AlarmTypeSpec]
    alias_index: dict[str, dict[str, str]]


class ParameterSchemaEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["integer", "number"]
    minimum: float | None = None
    maximum: float | None = None
    overridable: bool = False


class FeatureProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    description: str
    required_metrics: list[str]
    optional_metrics: list[str]
    algorithm_versions: dict[str, str]
    parameter_schema: dict[str, ParameterSchemaEntry]


class FeatureProfilesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["feature-profiles/1"]
    profiles: dict[str, FeatureProfile]


class ParameterSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_set_id: str
    version: str
    profile_id: str
    values: dict[str, Any]
    source: str
    reviewed_at: str


class ParameterOverride(ParameterSet):
    applies: dict[str, list[str]] = Field(min_length=1)


class ParameterSetsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["parameter-sets/1"]
    common_set: ParameterSet
    overrides: list[ParameterOverride] = Field(default_factory=list)
    fallback_set: ParameterSet


class PublishedScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    priority: int
    match: ScenarioMatch
    feature_profile: str
    profile_version: str
    include: list[str]
    snippet_versions: dict[str, str]
    expanded_steps: list[ExpandedStep]
    base_plan_hash: str


class PublishedFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    feature_profile: str
    profile_version: str
    expanded_steps: list[ExpandedStep]
    base_plan_hash: str


class BundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_version: str
    published_at: str
    source_hashes: dict[str, str]
    scenario_plan_hashes: dict[str, str]
    fallback_plan_hash: str
    dependencies: dict[str, list[str]]
    unreferenced_snippets: list[str]
    warnings: list[str]


class PublishedBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["published-bundle/1"]
    bundle_version: str
    limits: ExecutionLimits
    metric_dictionary: MetricDictionary
    alarm_dictionary: AlarmDictionary
    feature_profiles: FeatureProfilesFile
    parameter_sets: ParameterSetsFile
    scenarios: list[PublishedScenario]
    fallback: PublishedFallback
    manifest: BundleManifest
