"""已注册特征算法（§8.6、§8.9.9）。

所有算法只接受程序采集到的时序与已固定参数；缺少参数、样本或覆盖条件时
返回 unknown，不返回“正常”，也不把一次读数写成持续异常。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.evidence.collector import CollectedEvidence, PeerEvidence, SeriesEvidence
from app.schemas.analysis import EvidenceFeature, EvidenceSample
from app.schemas.execution import ExecutionTask, TimeWindow


@dataclass(frozen=True)
class NumericPoint:
    timestamp: datetime
    value: float


def numeric_points(samples: Iterable[EvidenceSample]) -> list[NumericPoint]:
    return [
        NumericPoint(sample.timestamp, float(sample.value))
        for sample in samples
        if isinstance(sample.value, (int, float)) and sample.quality not in {"bad", "missing"}
    ]


def split_segments(points: list[NumericPoint], max_gap_seconds: int) -> list[list[NumericPoint]]:
    if not points:
        return []
    ordered = sorted(points, key=lambda item: item.timestamp)
    segments: list[list[NumericPoint]] = [[ordered[0]]]
    for point in ordered[1:]:
        gap = (point.timestamp - segments[-1][-1].timestamp).total_seconds()
        if gap > max_gap_seconds:
            segments.append([point])
        else:
            segments[-1].append(point)
    return segments


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _parameter_ref(
    name: str,
    value: Any,
    unit: str | None,
    expanded_plan: dict[str, Any],
) -> dict[str, Any]:
    return {
        "parameter": name,
        "value": value,
        "unit": unit,
        "parameter_set_ids": list(expanded_plan.get("parameter_set_ids") or []),
        "sources": list(expanded_plan.get("parameter_sources") or []),
    }


def _feature(
    *,
    feature_id: str,
    name: str,
    status: str,
    evidence_ids: list[str],
    algorithm_version: str,
    reason: str,
    window: TimeWindow,
    value: float | str | None = None,
    unit: str | None = None,
    parameter_refs: list[dict[str, Any]] | None = None,
) -> EvidenceFeature:
    return EvidenceFeature(
        id=feature_id,
        name=name,
        status=status,  # type: ignore[arg-type]
        evidence_ids=evidence_ids,
        rule_version=algorithm_version,
        algorithm_version=algorithm_version,
        parameter_refs=parameter_refs or [],
        reason=reason,
        value=value,
        unit=unit,
        window_start=window.start,
        window_end=window.end,
    )


class FeatureComputer:
    def __init__(
        self,
        *,
        task: ExecutionTask,
        collected: CollectedEvidence,
    ):
        self.task = task
        self.collected = collected
        self.expanded = task.expanded_plan or {}
        self.parameters: dict[str, Any] = self.expanded.get("parameters") or {}
        self.profile = self.expanded.get("feature_profile") or "basic_observation_v1"
        self.versions: dict[str, str] = self.expanded.get("feature_algorithm_versions") or {}
        self.window = task.time_window
        self._seq = 0

    def compute(self) -> list[EvidenceFeature]:
        if self.window is None:
            return []
        if self.profile == "temperature_demo_v1":
            return self._temperature_features()
        return self._basic_features()

    # ---------- 温度专项 ----------

    def _temperature_features(self) -> list[EvidenceFeature]:
        features: list[EvidenceFeature] = []
        trigger_metric = (self.task.metric_binding or {}).get("metric_code") or "supply_air_temperature"
        series = self._trigger_series(trigger_metric)
        data_quality = self._data_quality(series)
        if data_quality:
            features.append(data_quality)
        features.append(self._sustained(series))
        features.append(self._local_elevation(series))
        if self._has_step("peer_evidence"):
            features.extend(self._peer_features(series))
        if self._has_step("related_evidence"):
            features.append(self._upstream_observation(trigger_metric))
        features.append(self._short_spike(series))
        return features

    def _trigger_series(self, metric_code: str) -> SeriesEvidence | None:
        series_list = self.collected.self_series.get(metric_code) or []
        return series_list[0] if series_list else None

    def _data_quality(self, series: SeriesEvidence | None) -> EvidenceFeature:
        self._seq += 1
        feature_id = f"ft_{self._seq:02d}"
        if series is None:
            return _feature(
                feature_id=feature_id,
                name="data_quality",
                status="unknown",
                evidence_ids=[],
                algorithm_version=self.versions.get("data_quality", "1.0"),
                reason="未取得触发指标的有效时序，无法判断数据质量",
                window=self.window,
            )
        if series.complete:
            status = "matched"
            reason = (
                f"缺失比例 {series.missing_ratio:.0%}，样本 {len(series.samples)} 个，"
                "数据覆盖满足本次参数要求"
            )
        else:
            status = "not_matched"
            reason = (
                f"缺失比例 {series.missing_ratio:.0%} 或序列不完整，"
                "质量未达到本次参数要求；缺失不能按正常处理"
            )
        return _feature(
            feature_id=feature_id,
            name="data_quality",
            status=status,
            evidence_ids=[series.fact_id],
            algorithm_version=self.versions.get("data_quality", "1.0"),
            reason=reason,
            window=self.window,
            value=round(series.missing_ratio, 4),
            unit="missing_ratio",
            parameter_refs=[
                _parameter_ref(
                    "max_missing_ratio", self.parameters.get("max_missing_ratio"), None, self.expanded
                )
            ],
        )

    def _sustained(self, series: SeriesEvidence | None) -> EvidenceFeature:
        self._seq += 1
        feature_id = f"ft_{self._seq:02d}"
        threshold = self.parameters.get("sustained_threshold_c")
        min_consecutive = int(self.parameters.get("sustained_min_consecutive_samples") or 0)
        if series is None or threshold is None or min_consecutive <= 0:
            return _feature(
                feature_id=feature_id,
                name="sustained_threshold",
                status="unknown",
                evidence_ids=[series.fact_id] if series else [],
                algorithm_version=self.versions.get("sustained_threshold", "1.0"),
                reason="缺少有效时序或适用阈值/连续样本参数，不能复算持续超限特征",
                window=self.window,
                parameter_refs=[
                    _parameter_ref("sustained_threshold_c", threshold, "℃", self.expanded),
                    _parameter_ref("sustained_min_consecutive_samples", min_consecutive, "个", self.expanded),
                ],
            )
        points = numeric_points(series.samples)
        if len(points) < int(self.parameters.get("min_sample_count") or 0) or series.missing_ratio > float(
            self.parameters.get("max_missing_ratio") or 0
        ):
            return _feature(
                feature_id=feature_id,
                name="sustained_threshold",
                status="unknown",
                evidence_ids=[series.fact_id],
                algorithm_version=self.versions.get("sustained_threshold", "1.0"),
                reason="样本数量或数据覆盖不足，持续超限特征只能标记未知",
                window=self.window,
                parameter_refs=[
                    _parameter_ref("sustained_threshold_c", threshold, "℃", self.expanded),
                    _parameter_ref("sustained_min_consecutive_samples", min_consecutive, "个", self.expanded),
                ],
            )
        segments = split_segments(points, int(self.parameters.get("max_gap_seconds") or 120))
        longest = 0
        longest_segment_start: datetime | None = None
        longest_segment_end: datetime | None = None
        for segment in segments:
            run: list[NumericPoint] = []
            for point in segment:
                if point.value >= float(threshold):
                    run.append(point)
                    if len(run) > longest:
                        longest = len(run)
                        longest_segment_start = run[0].timestamp
                        longest_segment_end = run[-1].timestamp
                else:
                    run = []
        matched = longest >= min_consecutive
        reason = (
            f"阈值 {threshold}℃，最长连续超限 {longest} 个样本"
            f"（要求至少 {min_consecutive} 个）"
        )
        if longest_segment_start and longest_segment_end:
            reason += (
                f"，区间 {longest_segment_start.isoformat()} 至 {longest_segment_end.isoformat()}"
            )
        return _feature(
            feature_id=feature_id,
            name="sustained_threshold",
            status="matched" if matched else "not_matched",
            evidence_ids=[series.fact_id],
            algorithm_version=self.versions.get("sustained_threshold", "1.0"),
            reason=reason,
            window=self.window,
            value=longest,
            unit="个连续样本",
            parameter_refs=[
                _parameter_ref("sustained_threshold_c", threshold, "℃", self.expanded),
                _parameter_ref("sustained_min_consecutive_samples", min_consecutive, "个", self.expanded),
                _parameter_ref("max_gap_seconds", self.parameters.get("max_gap_seconds"), "秒", self.expanded),
            ],
        )

    def _local_elevation(self, series: SeriesEvidence | None) -> EvidenceFeature:
        self._seq += 1
        feature_id = f"ft_{self._seq:02d}"
        delta = self.parameters.get("local_elevation_delta_c")
        min_consecutive = int(self.parameters.get("local_elevation_min_consecutive_samples") or 0)
        baseline_minutes = int(self.parameters.get("baseline_window_minutes") or 0)
        if series is None or delta is None or min_consecutive <= 0 or baseline_minutes <= 0:
            return _feature(
                feature_id=feature_id,
                name="local_temperature_elevation",
                status="unknown",
                evidence_ids=[series.fact_id] if series else [],
                algorithm_version=self.versions.get("local_elevation", "1.0"),
                reason="缺少有效时序或本机变化参数，不能计算局部升温特征",
                window=self.window,
                parameter_refs=[
                    _parameter_ref("local_elevation_delta_c", delta, "℃", self.expanded),
                    _parameter_ref("baseline_window_minutes", baseline_minutes, "分钟", self.expanded),
                ],
            )
        points = numeric_points(series.samples)
        baseline_end = self.window.start + timedelta(minutes=baseline_minutes)
        baseline = [point.value for point in points if point.timestamp < baseline_end]
        observation = [point for point in points if point.timestamp >= baseline_end]
        if len(baseline) < min_consecutive or len(observation) < min_consecutive:
            return _feature(
                feature_id=feature_id,
                name="local_temperature_elevation",
                status="unknown",
                evidence_ids=[series.fact_id],
                algorithm_version=self.versions.get("local_elevation", "1.0"),
                reason="基线或观测段样本不足，不能计算本机变化特征",
                window=self.window,
                parameter_refs=[
                    _parameter_ref("local_elevation_delta_c", delta, "℃", self.expanded),
                    _parameter_ref("baseline_window_minutes", baseline_minutes, "分钟", self.expanded),
                ],
            )
        baseline_mean = mean(baseline)
        threshold = baseline_mean + float(delta)
        segments = split_segments(observation, int(self.parameters.get("max_gap_seconds") or 120))
        longest = 0
        peak = max((point.value for point in observation), default=baseline_mean)
        for segment in segments:
            run = 0
            for point in segment:
                if point.value >= threshold:
                    run += 1
                    longest = max(longest, run)
                else:
                    run = 0
        matched = longest >= min_consecutive
        return _feature(
            feature_id=feature_id,
            name="local_temperature_elevation",
            status="matched" if matched else "not_matched",
            evidence_ids=[series.fact_id],
            algorithm_version=self.versions.get("local_elevation", "1.0"),
            reason=(
                f"基线均值 {baseline_mean:.2f}℃，观测段峰值 {peak:.2f}℃，"
                f"最长连续超过基线 {delta}℃ 的样本 {longest} 个"
                f"（要求至少 {min_consecutive} 个）"
            ),
            window=self.window,
            value=round(peak - baseline_mean, 2),
            unit="℃",
            parameter_refs=[
                _parameter_ref("local_elevation_delta_c", delta, "℃", self.expanded),
                _parameter_ref("baseline_window_minutes", baseline_minutes, "分钟", self.expanded),
                _parameter_ref(
                    "local_elevation_min_consecutive_samples", min_consecutive, "个", self.expanded
                ),
            ],
        )

    def _peer_features(self, subject: SeriesEvidence | None) -> list[EvidenceFeature]:
        peers = self.collected.peers
        evidence_ids = [peer.fact_id for peer in peers]
        self._seq += 1
        stability_id = f"ft_{self._seq:02d}"
        self._seq += 1
        common_id = f"ft_{self._seq:02d}"
        peer_delta = self.parameters.get("peer_delta_c")
        min_count = int(self.parameters.get("peer_min_count") or 0)
        min_consecutive = int(self.parameters.get("local_elevation_min_consecutive_samples") or 0)
        baseline_minutes = int(self.parameters.get("baseline_window_minutes") or 0)
        comparable = [
            peer
            for peer in peers
            if len(numeric_points(peer.samples)) >= max(2, min_consecutive)
            and peer.complete
        ]
        if not comparable or peer_delta is None or min_count <= 0:
            reason = "没有足够的完整对照设备，无法判断是否为本机局部变化"
            return [
                _feature(
                    feature_id=stability_id,
                    name="peer_stability",
                    status="unknown",
                    evidence_ids=evidence_ids,
                    algorithm_version=self.versions.get("peer_stability", "1.0"),
                    reason=reason,
                    window=self.window,
                ),
                _feature(
                    feature_id=common_id,
                    name="common_change",
                    status="unknown",
                    evidence_ids=evidence_ids,
                    algorithm_version=self.versions.get("common_change", "1.0"),
                    reason=reason,
                    window=self.window,
                ),
            ]
        if len(comparable) < min_count:
            reason = f"可比较对照设备仅 {len(comparable)} 台，少于要求的 {min_count} 台"
            return [
                _feature(
                    feature_id=stability_id,
                    name="peer_stability",
                    status="unknown",
                    evidence_ids=evidence_ids,
                    algorithm_version=self.versions.get("peer_stability", "1.0"),
                    reason=reason,
                    window=self.window,
                ),
                _feature(
                    feature_id=common_id,
                    name="common_change",
                    status="unknown",
                    evidence_ids=evidence_ids,
                    algorithm_version=self.versions.get("common_change", "1.0"),
                    reason=reason,
                    window=self.window,
                ),
            ]
        rising: list[PeerEvidence] = []
        max_peer_delta = 0.0
        for peer in comparable:
            points = numeric_points(peer.samples)
            baseline_end = self.window.start + timedelta(minutes=baseline_minutes)
            baseline = [item.value for item in points if item.timestamp < baseline_end]
            observation = [item for item in points if item.timestamp >= baseline_end]
            if not baseline or not observation:
                continue
            baseline_mean = mean(baseline)
            peak = max(item.value for item in observation)
            peer_change = peak - baseline_mean
            max_peer_delta = max(max_peer_delta, peer_change)
            if peer_change >= float(peer_delta):
                rising.append(peer)
        stable = not rising
        common = len(rising) >= min_count
        return [
            _feature(
                feature_id=stability_id,
                name="peer_stability",
                status="matched" if stable else "not_matched",
                evidence_ids=evidence_ids,
                algorithm_version=self.versions.get("peer_stability", "1.0"),
                reason=(
                    f"可比较对照设备 {len(comparable)} 台，最大变化 {max_peer_delta:.2f}℃；"
                    + ("未出现超过阈值的同向升温" if stable else "存在同向升温，不能支持仅本机变化")
                ),
                window=self.window,
                value=round(max_peer_delta, 2),
                unit="℃",
                parameter_refs=[
                    _parameter_ref("peer_delta_c", peer_delta, "℃", self.expanded),
                    _parameter_ref("peer_min_count", min_count, "台", self.expanded),
                ],
            ),
            _feature(
                feature_id=common_id,
                name="common_change",
                status="matched" if common else "not_matched",
                evidence_ids=evidence_ids,
                algorithm_version=self.versions.get("common_change", "1.0"),
                reason=(
                    f"{len(rising)} 台对照设备出现超过 {peer_delta}℃ 的升温"
                    f"（要求至少 {min_count} 台）"
                ),
                window=self.window,
                value=len(rising),
                unit="台",
                parameter_refs=[
                    _parameter_ref("peer_delta_c", peer_delta, "℃", self.expanded),
                    _parameter_ref("peer_min_count", min_count, "台", self.expanded),
                ],
            ),
        ]

    def _upstream_observation(self, trigger_metric: str) -> EvidenceFeature:
        self._seq += 1
        feature_id = f"ft_{self._seq:02d}"
        related = [
            series
            for series in self.collected.related_series
            if series.metric_code != trigger_metric and numeric_points(series.samples)
        ]
        if not related:
            return _feature(
                feature_id=feature_id,
                name="upstream_cooling_observation",
                status="unknown",
                evidence_ids=[],
                algorithm_version=self.versions.get("upstream_observation", "1.0"),
                reason="已配置上游供冷取证，但未取得可用的上游读数；不能认定上游正常",
                window=self.window,
            )
        series = related[0]
        points = numeric_points(series.samples)
        values = [point.value for point in points]
        return _feature(
            feature_id=feature_id,
            name="upstream_cooling_observation",
            status="matched",
            evidence_ids=[series.fact_id],
            algorithm_version=self.versions.get("upstream_observation", "1.0"),
            reason=(
                f"已取得 {series.asset_name} 的 {series.metric_name} 读数，"
                f"窗口均值 {mean(values):.2f}{series.unit or ''}；仅作观测，不据此确认冷源故障"
            ),
            window=self.window,
            value=round(mean(values), 2),
            unit=series.unit,
            parameter_refs=[],
        )

    def _short_spike(self, series: SeriesEvidence | None) -> EvidenceFeature:
        self._seq += 1
        feature_id = f"ft_{self._seq:02d}"
        delta = self.parameters.get("short_spike_delta_c")
        max_minutes = int(self.parameters.get("short_spike_max_duration_minutes") or 0)
        if series is None or delta is None or max_minutes <= 0:
            return _feature(
                feature_id=feature_id,
                name="short_spike",
                status="unknown",
                evidence_ids=[series.fact_id] if series else [],
                algorithm_version=self.versions.get("short_spike", "1.0"),
                reason="缺少有效时序或跳变参数，不能判断短时跳变特征",
                window=self.window,
            )
        if not series.complete or series.missing_ratio > float(self.parameters.get("max_missing_ratio") or 0):
            return _feature(
                feature_id=feature_id,
                name="short_spike",
                status="unknown",
                evidence_ids=[series.fact_id],
                algorithm_version=self.versions.get("short_spike", "1.0"),
                reason="数据存在缺口或覆盖不足，不能判断跳变后是否回落",
                window=self.window,
            )
        points = numeric_points(series.samples)
        max_jump = 0.0
        matched = False
        for index in range(1, len(points)):
            previous = points[index - 1]
            current = points[index]
            jump = current.value - previous.value
            max_jump = max(max_jump, jump)
            if jump < float(delta):
                continue
            deadline = current.timestamp + timedelta(minutes=max_minutes)
            for later in points[index + 1 :]:
                if later.timestamp > deadline:
                    break
                if later.value <= previous.value + 0.5:
                    matched = True
                    break
            if matched:
                break
        return _feature(
            feature_id=feature_id,
            name="short_spike",
            status="matched" if matched else "not_matched",
            evidence_ids=[series.fact_id],
            algorithm_version=self.versions.get("short_spike", "1.0"),
            reason=(
                f"最大相邻跃升 {max_jump:.2f}℃，跃升阈值 {delta}℃；"
                + ("观察到跃升后在时限内回落" if matched else "未观察到跃升后在时限内回落")
            ),
            window=self.window,
            value=round(max_jump, 2),
            unit="℃",
            parameter_refs=[
                _parameter_ref("short_spike_delta_c", delta, "℃", self.expanded),
                _parameter_ref("short_spike_max_duration_minutes", max_minutes, "分钟", self.expanded),
            ],
        )

    # ---------- 本机兜底 ----------

    def _basic_features(self) -> list[EvidenceFeature]:
        trigger_metric = (self.task.metric_binding or {}).get("metric_code") or ""
        series = self._trigger_series(trigger_metric)
        features = []
        quality = self._data_quality(series)
        features.append(quality)
        self._seq += 1
        feature_id = f"ft_{self._seq:02d}"
        if series is None:
            features.append(
                _feature(
                    feature_id=feature_id,
                    name="reading_summary",
                    status="unknown",
                    evidence_ids=[],
                    algorithm_version=self.versions.get("reading_summary", "1.0"),
                    reason="未取得触发指标读数，不能整理本机观测摘要",
                    window=self.window,
                )
            )
        else:
            points = numeric_points(series.samples)
            values = [point.value for point in points]
            features.append(
                _feature(
                    feature_id=feature_id,
                    name="reading_summary",
                    status="matched" if values else "unknown",
                    evidence_ids=[series.fact_id],
                    algorithm_version=self.versions.get("reading_summary", "1.0"),
                    reason=(
                        f"本机 {series.metric_name} 样本 {len(values)} 个，均值 "
                        f"{mean(values):.2f}{series.unit or ''}；仅作观测，不作跨设备根因判断"
                        if values
                        else "读数不是数值类型，仅保留原始观测"
                    ),
                    window=self.window,
                    value=round(mean(values), 2) if values else None,
                    unit=series.unit,
                )
            )
        return features

    def _has_step(self, action: str) -> bool:
        return any(step.get("action") == action for step in self.expanded.get("steps") or [])
