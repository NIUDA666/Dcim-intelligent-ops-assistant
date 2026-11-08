/*
 * 前端共享层：格式化、结构化结果渲染、会话与 SSE 控制器。
 * 调试控制台（debug.js）与 DCIM 悬浮助手（dcim.js）复用同一套渲染与查询状态机，
 * 保证两种外壳对同一份后端事件给出相同的事实表达。
 */

const TZ = "Asia/Shanghai";
const DATE_TIME_FMT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const DATE_TIME_SHORT_FMT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TZ,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const HOUR_MINUTE_FMT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export const STATUS_LABELS = {
  accepted: "已受理",
  pending: "待执行",
  ready: "待执行",
  running: "执行中",
  success: "成功",
  completed: "完成",
  empty: "无数据",
  partial: "部分完成",
  error: "失败",
  failed: "失败",
  needs_confirmation: "待确认",
  skipped_condition: "条件跳过",
  blocked: "已阻断",
  cancelled: "已取消",
  waiting_user: "待补充信息",
  waiting_dependency: "等待上游",
  done: "已完成",
};

export const OVERALL_LABELS = {
  accepted: "已受理",
  running: "执行中",
  success: "查询完成",
  partial: "部分完成",
  waiting_user: "待补充信息",
  failed: "查询失败",
  cancelled: "已取消",
};

export const RESULT_TYPE_LABELS = {
  metric_latest: "指标最新值",
  metric_series: "指标趋势",
  alarm_list: "告警列表",
  alarm_count: "告警计数",
  knowledge_answer: "知识问答",
  alarm_analysis: "辅助分析",
  skipped_condition: "条件跳过",
  error: "失败结果",
};

export const COUNT_POLICY = {
  new_occurrences_in_window: {
    title: "窗口内新发生次数",
    unit: "次",
    note: "按独立 episode 统计：同一告警重复提醒不重复计数；恢复后再次触发按新事件计数。该口径与“当前活动告警”不同。",
  },
  active_at_query_time: {
    title: "当前活动数量",
    unit: "条",
    note: "按查询时点仍处于未恢复状态的告警条数统计。该口径与“窗口内新发生次数”不同，不能混用。",
  },
  recovered_in_window: {
    title: "窗口内恢复数量",
    unit: "次",
    note: "统计窗口内恢复的独立 episode，重复提醒不重复计数。",
  },
};

const ASSERTION_LABELS = {
  reported_by_platform: { label: "平台报告", cls: "badge-platform" },
  confirmed_abnormal: { label: "后端确认异常", cls: "badge-danger" },
  confirmed_normal: { label: "后端确认正常", cls: "badge-ok" },
  unknown: { label: "结论未知", cls: "badge-warn" },
};

const FEATURE_LABELS = {
  matched: { label: "命中", cls: "badge-danger" },
  not_matched: { label: "未命中", cls: "badge-muted" },
  unknown: { label: "未知", cls: "badge-warn" },
};

const WARNING_LABELS = {
  series_incomplete: "序列不完整（缺失或采样质量不足）",
  latest_reading_stale: "最新读数已超过新鲜度阈值",
  no_alarm_records: "查询成功，但记录范围内没有告警",
  count_incomplete: "计数来源不完整，不能给出完整总数",
  empty_time_window: "时间窗口为空",
  no_knowledge_found: "知识库没有检索到已审核且当前有效的片段",
  faq_skipped_multi_task: "多子任务请求未启用 FAQ 快速通道",
  faq_skipped_not_device_independent_low_risk: "问题涉及设备或风险信息，未启用 FAQ 快速通道",
  faq_candidate_rejected: "FAQ 候选未通过来源校验，已转正式检索",
  peer_point_unavailable: "对比设备缺少该指标测点",
  peer_data_unavailable: "对比设备数据不可用",
  peer_no_numeric_data: "对比设备没有有效数值",
  related_asset_budget_reached: "关联设备取证数量达到预算上限",
  topology_snapshot_only: "仅取得当前拓扑快照，不是告警时点快照",
  work_order_unavailable: "工单/检修记录不可用",
  fan_feedback_missing: "缺少风机反馈等关键证据",
  analysis_partial: "证据不完整，分析结论为部分完成",
  topology_snapshot_is_current_only: "拓扑快照为当前状态，不能证明异常时点的历史连接",
};

const SOP_PURPOSE_LABELS = {
  initial_checks: "初步核查",
  escalation: "升级处置",
  verification: "结果复核",
};

const CONFIRMATION_LABELS = {
  pending_field_check: "待现场核查",
  confirmed_by_field: "已现场确认",
};

const GAP_TYPE_LABELS = {
  data_gap: "数据缺口",
  evidence_gap: "证据缺口",
  topology_gap: "拓扑缺口",
  knowledge_gap: "资料缺口",
  missing_work_order: "工单缺口",
  missing_tool_data: "采集缺口",
};

const HYPOTHESIS_ASSESSMENT = {
  supported_direction: "支持该排查方向（未确认根因）",
  insufficient_evidence: "证据不足",
  contradicted: "存在冲突证据",
  rejected: "不支持该方向",
};

export function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// 报告正文是受控 Markdown。先整体转义，再补结构标签，避免把模型输出当作 HTML。
export function renderRichText(value) {
  const source = String(value ?? "").replace(/\r\n?/g, "\n").trim();
  if (!source) return "";

  const inline = (segment) =>
    esc(segment)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([A-Za-z][A-Za-z0-9_#.-]*)\]/g, '<span class="cite">[$1]</span>');

  const html = [];
  let pending = [];
  let list = null;

  const flushParagraph = () => {
    if (!pending.length) return;
    html.push(`<p>${pending.map(inline).join("<br>")}</p>`);
    pending = [];
  };
  const closeList = () => {
    if (!list) return;
    html.push(`</${list}>`);
    list = null;
  };
  const openList = (tag) => {
    if (list === tag) return;
    closeList();
    html.push(`<${tag}>`);
    list = tag;
  };

  source.split("\n").forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      closeList();
      return;
    }
    if (line === "---") {
      flushParagraph();
      closeList();
      html.push("<hr>");
      return;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = Math.min(5, heading[1].length + 2);
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      return;
    }
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph();
      openList("ul");
      html.push(`<li>${inline(bullet[1])}</li>`);
      return;
    }
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    if (numbered) {
      flushParagraph();
      openList("ol");
      html.push(`<li>${inline(numbered[1])}</li>`);
      return;
    }
    closeList();
    pending.push(line);
  });

  flushParagraph();
  closeList();
  return html.join("");
}

function isNum(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function toNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

export function fmtNum(value, digits = 2) {
  if (!isNum(value)) return value === null || value === undefined ? "—" : String(value);
  return value
    .toFixed(digits)
    .replace(/\.?0+$/, "");
}

export function fmtPct(value, digits = 2) {
  if (!isNum(value)) return "—";
  return `${fmtNum(value * 100, digits)}%`;
}

export function fmtDateTime(value, withSeconds = true) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const parts = Object.fromEntries(
    DATE_TIME_FMT.formatToParts(date).map((part) => [part.type, part.value]),
  );
  const time = `${parts.hour === "24" ? "00" : parts.hour}:${parts.minute}${withSeconds ? `:${parts.second}` : ""}`;
  return `${parts.year}-${parts.month}-${parts.day} ${time}`;
}

export function fmtWindow(window) {
  if (!window || !window.start || !window.end) return null;
  return `${fmtDateTime(window.start, false)} → ${fmtDateTime(window.end, false)}`;
}

export function shortHash(value) {
  if (!value) return "—";
  const text = String(value);
  return text.length > 14 ? `${text.slice(0, 12)}…` : text;
}

export function humanWarning(code) {
  if (!code) return "";
  const text = String(code);
  if (WARNING_LABELS[text]) return WARNING_LABELS[text];
  const [prefix, ...rest] = text.split(":");
  if (WARNING_LABELS[prefix]) {
    return rest.length ? `${WARNING_LABELS[prefix]}（${rest.join(":")}）` : WARNING_LABELS[prefix];
  }
  return text;
}

export function statusBadgeClass(status) {
  if (status === "success") return "badge-ok";
  if (status === "partial" || status === "skipped_condition") return "badge-warn";
  if (status === "error" || status === "failed" || status === "blocked") return "badge-danger";
  if (status === "running") return "badge-platform";
  if (status === "empty" || status === "cancelled") return "badge-muted";
  if (status === "needs_confirmation" || status === "waiting_user") return "badge-warn";
  return "badge-muted";
}

export function statusClass(status) {
  if (status === "success") return "success";
  if (status === "partial" || status === "waiting_user") return "partial";
  if (status === "failed" || status === "error") return "failed";
  if (status === "cancelled") return "cancelled";
  return "running";
}

export function answerPillClass(status) {
  if (status === "success") return "pill pill-ok";
  if (status === "partial" || status === "waiting_user") return "pill pill-warn";
  if (status === "failed") return "pill pill-danger";
  return "pill";
}

export function makeId(prefix) {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return `${prefix}-${window.crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function request(path, options = {}) {
  const { method = "GET", body, headers = {} } = options;
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail;
    const error = payload && payload.error;
    const source = detail !== undefined && detail !== null ? detail : error;
    const message =
      typeof source === "string"
        ? source
        : (source && source.message) || (payload && payload.message) || `请求失败（HTTP ${response.status}）`;
    const failure = new Error(message);
    failure.status = response.status;
    failure.code =
      (source && typeof source === "object" && source.code) || (error && error.code) || null;
    throw failure;
  }
  return payload;
}

export async function submitFeedback(requestId, label, taskId = null) {
  return request("/api/v1/feedback", {
    method: "POST",
    body: { request_id: requestId, task_id: taskId, label },
  });
}

/* ------------------------------------------------------------------ */
/* 结构化结果渲染                                                       */
/* ------------------------------------------------------------------ */

function section(title, content) {
  if (!content) return "";
  return `<div class="section-title">${esc(title)}</div>${content}`;
}

function notes(items, cls = "warn") {
  const cleaned = (items || []).map(humanWarning).filter(Boolean);
  if (!cleaned.length) return "";
  return `<div class="task-note ${cls}">${cleaned.map(esc).join("；")}</div>`;
}

function kvGrid(items) {
  const rows = (items || []).filter((item) => item && item.v !== undefined && item.v !== null);
  if (!rows.length) return "";
  return `<div class="kv-grid">${rows
    .map(
      (item) => `<div class="kv"><div class="k">${esc(item.k)}</div><div class="v">${esc(item.v)}</div></div>`,
    )
    .join("")}</div>`;
}

function paramChips(refs) {
  const list = (refs || []).map((ref) => {
    if (ref && typeof ref === "object") {
      if ("parameter" in ref) {
        const unit = ref.unit && ref.unit !== "null" ? ` ${ref.unit}` : "";
        const extras = [];
        if (Array.isArray(ref.parameter_set_ids) && ref.parameter_set_ids.length) {
          extras.push(`参数集：${ref.parameter_set_ids.join(", ")}`);
        }
        if (Array.isArray(ref.sources) && ref.sources.length) {
          extras.push(`来源：${ref.sources.join("；")}`);
        }
        return {
          text: `${ref.parameter}=${ref.value ?? "—"}${unit}`,
          title: extras.join("\n"),
        };
      }
      if ("name" in ref && "value" in ref) {
        return { text: `${ref.name}=${ref.value}`, title: "" };
      }
      return {
        text: Object.entries(ref)
          .map(([key, value]) => {
            const cell = value && typeof value === "object" ? JSON.stringify(value) : String(value);
            return `${key}=${cell}`;
          })
          .join(" · "),
        title: "",
      };
    }
    return { text: String(ref), title: "" };
  });
  if (!list.length) return "";
  return `<div class="param-chips">${list
    .map(
      (item) =>
        `<span class="param-chip"${item.title ? ` title="${esc(item.title)}"` : ""}>${esc(item.text)}</span>`,
    )
    .join("")}</div>`;
}

export function renderTaskCard(card, options = {}) {
  const element = document.createElement("article");
  element.className = `task-card${options.compact ? " task-card-compact" : ""}`;
  element.dataset.taskId = card.task_id;
  const devices = (card.device_refs || [])
    .map((ref) => ref.name || ref.device_code || ref.asset_id)
    .filter(Boolean);
  const window = card.time_window || (card.result && card.result.requested_window);
  const subParts = [];
  if (devices.length) subParts.push(esc(devices.join("、")));
  if (window) subParts.push(esc(fmtWindow(window)));
  else if (card.intent === "alarm_list" && card.data && card.data.policy === "active_at_query_time") {
    subParts.push("查询时点口径");
  }
  subParts.push(`<span class="mono">${esc(card.task_id)}</span>`);
  if (options.compact) subParts.pop();
  const badges = [
    `<span class="badge ${statusBadgeClass(card.status)}">${esc(STATUS_LABELS[card.status] || card.status)}</span>`,
    card.result_type
      ? `<span class="badge badge-muted">${esc(RESULT_TYPE_LABELS[card.result_type] || card.result_type)}</span>`
      : "",
    card.source_mode ? `<span class="badge badge-mock">${esc(card.source_mode)} 来源</span>` : "",
  ].filter(Boolean);
  element.innerHTML = `
    <div class="task-head">
      <div>
        <h3>${esc(card.title || card.task_id)}</h3>
        <div class="task-sub">${subParts.join("<span>·</span>")}</div>
      </div>
      <div class="task-badges">${badges.join("")}</div>
    </div>
    <div class="task-body">${renderCardBody(card, options)}</div>
  `;
  return element;
}

function renderCardBody(card, options = {}) {
  const bodyNote = (content) => `${content}${notes(card.warnings || [])}`;
  if (card.status === "pending") {
    return bodyNote(
      '<div class="task-note">等待执行。模型只输出语义计划，后端将按已发布配置展开取证。</div>',
    );
  }
  if (card.status === "running") {
    return bodyNote('<div class="task-note">正在执行后端固定取证计划…</div>');
  }
  if (card.status === "error") {
    const message = (card.warnings || []).map(humanWarning).join("；") || "子任务执行失败";
    const code = options.compact ? "" : `<b>${esc(card.error_code || "TASK_FAILED")}</b>：`;
    return bodyNote(
      `<div class="task-note danger">${code}${esc(message)}。该项没有可用结果，不能改写成正常、零值或无异常。</div>`,
    );
  }
  if (card.status === "skipped_condition") {
    const reason = (card.data && card.data.reason) || (card.warnings || []).join("；");
    return bodyNote(
      `<div class="task-note warn">未执行后续分析：${esc(reason || "未满足执行条件")}。条件跳过不代表设备正常。</div>`,
    );
  }
  if (card.status === "waiting_user") {
    return bodyNote(
      `<div class="task-note warn">${esc(card.clarification || "需要补充查询信息后才能继续取证。")}</div>`,
    );
  }
  if (card.status === "blocked" || card.status === "needs_confirmation") {
    const message = card.clarification || "等待对象确认或上游结果";
    return bodyNote(
      `<div class="task-note warn">该子任务尚未执行：${esc(message)}。系统不会用猜测的设备或测点继续取证。</div>`,
    );
  }
  if (card.status === "cancelled") {
    return bodyNote('<div class="task-note">子任务已取消，未形成结果。</div>');
  }
  if (card.intent === "metric_latest") return bodyNote(renderLatest(card));
  if (card.intent === "metric_trend") return bodyNote(renderTrend(card, options));
  if (card.intent === "alarm_count") return bodyNote(renderAlarmCount(card));
  if (card.intent === "alarm_list") return bodyNote(renderAlarmList(card));
  if (card.intent === "knowledge_qa") return bodyNote(renderKnowledge(card));
  if (card.intent === "alarm_analysis") {
    return bodyNote(options.compact ? renderAnalysisCompact(card) : renderAnalysis(card));
  }
  return bodyNote('<div class="task-note">该任务没有可展示的结构化结果。</div>');
}

function renderLatest(card) {
  const data = card.data || {};
  if (card.status === "empty" || data.value === null || data.value === undefined) {
    return '<div class="task-note warn">没有取得有效最新读数，不能据此判断设备正常。</div>';
  }
  const unit = data.unit || card.unit || "";
  const quality = data.quality || "unknown";
  const qualityBadge =
    quality === "good"
      ? '<span class="badge badge-ok">good</span>'
      : `<span class="badge badge-warn">${esc(quality)}</span>`;
  return `
    <div class="metric-hero">
      <span class="value">${esc(fmtNum(data.value))}</span>
      <span class="unit">${esc(unit)}</span>
      <span>${qualityBadge}</span>
    </div>
    ${kvGrid([
      { k: "采集时间", v: fmtDateTime(data.timestamp) },
      { k: "资产 asset_id", v: data.asset_id || "—" },
      { k: "测点 point_id", v: data.point_id || "—" },
      { k: "指标编码", v: data.metric_code || "—" },
      { k: "来源", v: data.source_system || card.source_mode || "—" },
      { k: "聚合", v: card.aggregation || "latest" },
    ])}
    <p class="source-note">“现在”使用固定查询锚点读取最近一条记录；过期读数会标注 stale，不当作实时正常值。</p>
  `;
}

function chartTime(dateMs, spanMs) {
  const date = new Date(dateMs);
  const pattern = spanMs > 24 * 3600 * 1000 ? DATE_TIME_SHORT_FMT : HOUR_MINUTE_FMT;
  const parts = Object.fromEntries(pattern.formatToParts(date).map((part) => [part.type, part.value]));
  const hour = parts.hour === "24" ? "00" : parts.hour;
  return pattern === HOUR_MINUTE_FMT
    ? `${hour}:${parts.minute}`
    : `${parts.month}-${parts.day} ${hour}:${parts.minute}`;
}

const METRIC_LABELS = {
  supply_air_temperature: "送风温度",
  return_air_temperature: "回风温度",
  chilled_water_supply_temperature: "冷冻水供水温度",
};

function trendSeries(card) {
  const data = card.data || {};
  const rawPoints = Array.isArray(data.points) ? data.points : [];
  const points = rawPoints
    .map((point) => ({
      t: Date.parse(point.timestamp),
      v: toNumber(point.value),
      q: point.quality || "good",
    }))
    .filter((point) => Number.isFinite(point.t) && point.v !== null)
    .sort((a, b) => a.t - b.t);
  const values = points.map((point) => point.v);
  const unit = data.unit || card.unit || "";
  const window = card.time_window || (card.result && card.result.requested_window);
  return {
    data,
    points,
    unit,
    window,
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
    mean: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
  };
}

function renderTrend(card, options = {}) {
  const { data, points, unit, window, min, max, mean } = trendSeries(card);
  if (!points.length) {
    return '<div class="task-note warn">核查窗口内没有有效样本，不能据此判断正常。</div>';
  }
  const summary = `
    <div class="metric-meta">
      <span>样本 <b>${points.length}</b>${data.expected_points ? ` / 期望 ${data.expected_points}` : ""}</span>
      <span>最小 <b>${esc(fmtNum(min))}</b> ${esc(unit)}</span>
      <span>最大 <b>${esc(fmtNum(max))}</b> ${esc(unit)}</span>
      <span>均值 <b>${esc(fmtNum(mean))}</b> ${esc(unit)}</span>
      <span>缺失率 <b>${esc(fmtPct(data.missing_ratio))}</b></span>
    </div>
  `;
  if (options.compact) {
    return `
      ${summary}
      ${kvGrid([
        { k: "窗口", v: fmtWindow(window) || "—" },
        { k: "实际 / 期望点", v: `${data.actual_points ?? points.length} / ${data.expected_points ?? "—"}` },
        { k: "采样间隔", v: data.sampling_interval_seconds ? `${data.sampling_interval_seconds} 秒` : "—" },
        { k: "数据完整性", v: card.complete ? "完整" : "不完整" },
      ])}
      <p class="source-note">曲线显示在回答下方；原始样本与测点信息保留在调试控制台。</p>
    `;
  }
  const qualityParts = Object.entries(data.quality_counts || {})
    .map(([quality, count]) => `${quality} ${count}`)
    .join(" / ");
  return `
    ${summary}
    ${renderTrendChart(points, unit, window, data.sampling_interval_seconds)}
    <div class="chart-legend">
      <span><i class="legend-line"></i>有效读数</span>
      <span><i class="legend-gap"></i>采样中断（不连线）</span>
      <span>琥珀点：非 good 质量</span>
      ${qualityParts ? `<span>质量分布：${esc(qualityParts)}</span>` : ""}
    </div>
    ${kvGrid([
      { k: "窗口", v: fmtWindow(window) || "—" },
      { k: "实际 / 期望点", v: `${data.actual_points ?? points.length} / ${data.expected_points ?? "—"}` },
      { k: "采样间隔", v: data.sampling_interval_seconds ? `${data.sampling_interval_seconds} 秒` : "—" },
      { k: "数据完整性", v: card.complete ? "完整" : "不完整" },
      { k: "资产 / 测点", v: `${data.asset_id || "—"} / ${data.point_id || "—"}` },
    ])}
    <p class="source-note">折线按原始样本绘制；采样间隔超过阈值或质量非 good 时断开，不跨缺口连线。</p>
  `;
}

function renderChartCard(title, points, unit, window, intervalSeconds, subtitle = "") {
  return `
    <div class="visual-head">
      <b>${esc(title)}</b>
      <span>${esc(subtitle || fmtWindow(window) || "")}</span>
    </div>
    ${renderTrendChart(points, unit, window, intervalSeconds)}
    <div class="chart-legend">
      <span><i class="legend-line"></i>有效读数</span>
      <span><i class="legend-gap"></i>采样中断（不连线）</span>
      <span>琥珀点：非 good 质量</span>
    </div>
  `;
}

export function renderTrendVisual(card) {
  const { data, points, unit, window } = trendSeries(card);
  if (!points.length) return "";
  return renderChartCard(
    card.title || "指标趋势",
    points,
    unit,
    window,
    data.sampling_interval_seconds,
  );
}

export function renderAnalysisTrend(card) {
  const bundle = card.analysis;
  if (!bundle) return "";
  const fact = (bundle.facts || []).find(
    (item) => item.kind === "timeseries" && Array.isArray(item.data) && item.data.length,
  );
  if (!fact) return "";
  const points = fact.data
    .map((sample) => ({
      t: Date.parse(sample.timestamp),
      v: toNumber(sample.value),
      q: sample.quality || "good",
    }))
    .filter((point) => Number.isFinite(point.t) && point.v !== null)
    .sort((a, b) => a.t - b.t);
  if (!points.length) return "";
  const window =
    bundle.evidence_window ||
    card.time_window ||
    (card.result && card.result.requested_window);
  const title = `${METRIC_LABELS[fact.metric_code] || "本机指标"}趋势`;
  return renderChartCard(title, points, fact.unit || "", window, null);
}

function renderTrendChart(points, unit, window, intervalSeconds) {
  const W = 760;
  const H = 260;
  const L = 54;
  const R = 16;
  const T = 16;
  const B = 34;
  const plotW = W - L - R;
  const plotH = H - T - B;
  let start = window && window.start ? Date.parse(window.start) : points[0].t;
  let end = window && window.end ? Date.parse(window.end) : points[points.length - 1].t;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    start = points[0].t;
    end = points[points.length - 1].t;
  }
  if (end <= start) end = start + 60000;
  const values = points.map((point) => point.v);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    const padding = Math.max(1, Math.abs(min) * 0.1);
    min -= padding;
    max += padding;
  } else {
    const padding = (max - min) * 0.12;
    min -= padding;
    max += padding;
  }
  const x = (time) => L + ((time - start) / (end - start)) * plotW;
  const y = (value) => T + ((max - value) / (max - min)) * plotH;
  const gapLimit = Math.max((Number(intervalSeconds) || 60) * 1750, 90000);

  const segments = [];
  let segment = [];
  let previous = null;
  const gaps = [];
  for (const point of points) {
    if (previous && point.t - previous.t > gapLimit) {
      if (segment.length) segments.push(segment);
      segment = [];
      gaps.push([previous, point]);
    }
    segment.push(point);
    if (point.q !== "good") {
      if (segment.length) segments.push(segment);
      segment = [];
    }
    previous = point;
  }
  if (segment.length) segments.push(segment);

  const gridLines = [];
  const yLabels = [];
  for (let index = 0; index <= 4; index += 1) {
    const value = max - ((max - min) * index) / 4;
    const yy = T + (plotH * index) / 4;
    gridLines.push(`<line class="chart-grid" x1="${L}" y1="${yy.toFixed(1)}" x2="${W - R}" y2="${yy.toFixed(1)}"></line>`);
    yLabels.push(
      `<text class="chart-axis" x="${L - 8}" y="${(yy + 3).toFixed(1)}" text-anchor="end">${esc(fmtNum(value, 1))}</text>`,
    );
  }
  const span = end - start;
  for (let index = 0; index <= 3; index += 1) {
    const time = start + (span * index) / 3;
    const xx = L + (plotW * index) / 3;
    gridLines.push(`<line class="chart-grid" x1="${xx.toFixed(1)}" y1="${T}" x2="${xx.toFixed(1)}" y2="${T + plotH}"></line>`);
    yLabels.push(
      `<text class="chart-axis" x="${xx.toFixed(1)}" y="${H - 12}" text-anchor="${index === 0 ? "start" : index === 3 ? "end" : "middle"}">${esc(chartTime(time, span))}</text>`,
    );
  }

  const gapLines = gaps
    .map(
      ([left, right]) =>
        `<line class="chart-gap" x1="${x(left.t).toFixed(1)}" y1="${y(left.v).toFixed(1)}" x2="${x(right.t).toFixed(1)}" y2="${y(right.v).toFixed(1)}"></line>`,
    )
    .join("");

  const drawn = segments
    .map((part) => {
      const pointsText = part
        .map((point) => `${x(point.t).toFixed(1)},${y(point.v).toFixed(1)}`)
        .join(" ");
      const polyline =
        part.length > 1 ? `<polyline class="chart-line" points="${pointsText}"></polyline>` : "";
      const dots = part
        .map((point) => {
          const klass = point.q === "good" ? "chart-dot" : "chart-dot-warn";
          const radius = point.q === "good" ? 2 : 3.2;
          return `<circle class="${klass}" cx="${x(point.t).toFixed(1)}" cy="${y(point.v).toFixed(1)}" r="${radius}"></circle>`;
        })
        .join("");
      return polyline + dots;
    })
    .join("");

  return `
    <div class="chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="指标趋势图，共 ${points.length} 个样本，单位 ${esc(unit || "无")}">
        ${gridLines.join("")}
        ${gapLines}
        ${drawn}
        ${yLabels.join("")}
      </svg>
    </div>
  `;
}

function renderAlarmCount(card) {
  const data = card.data || {};
  const policy = COUNT_POLICY[data.policy] || COUNT_POLICY.new_occurrences_in_window;
  const count = isNum(data.count) ? data.count : null;
  const byType = Array.isArray(data.by_alarm_type) ? data.by_alarm_type : [];
  const table = byType.length
    ? `<table class="data-table">
        <thead><tr><th>告警类型</th><th>计数</th><th>观察到的独立事件</th></tr></thead>
        <tbody>${byType
          .map(
            (item) =>
              `<tr><td><b>${esc(item.alarm_type_id)}</b></td><td>${esc(item.count ?? "—")}</td><td>${esc(item.observed_distinct ?? "—")}</td></tr>`,
          )
          .join("")}</tbody>
      </table>`
    : "";
  return `
    <div class="metric-hero">
      <span class="value">${count === null ? "—" : esc(String(count))}</span>
      <span class="unit">${policy.unit}</span>
    </div>
    <div class="metric-meta">
      <span><b>${esc(policy.title)}</b></span>
      <span>观察到的独立事件 <b>${esc(data.observed_distinct ?? "—")}</b></span>
      <span>统计完整性 <b>${data.complete ? "完整" : "不完整"}</b></span>
    </div>
    ${kvGrid([
      { k: "时点口径", v: data.policy || "—" },
      { k: "统计窗口", v: fmtWindow(data.window || (card.result && card.result.requested_window)) || "查询时点" },
      { k: "查询锚点", v: fmtDateTime(data.query_time) },
    ])}
    ${table}
    <div class="task-note ${data.complete ? "" : "warn"}">${esc(policy.note)}${data.complete ? "" : " 计数来源不完整，不能给出完整总数。"}</div>
  `;
}

function renderAlarmList(card) {
  const data = card.data || {};
  const episodes = Array.isArray(data.episodes) ? data.episodes : [];
  const count = isNum(data.count) ? data.count : episodes.length;
  if (!episodes.length) {
    return `
      <div class="metric-hero"><span class="value">0</span><span class="unit">条</span></div>
      <div class="task-note">查询成功，当前没有未恢复告警。该结论仅表示平台记录范围内没有活动告警，不等于设备正常。</div>
    `;
  }
  return `
    <div class="metric-hero"><span class="value">${esc(String(count))}</span><span class="unit">条</span></div>
    <div class="metric-meta">
      <span>口径 <b>${data.policy === "active_at_query_time" ? "查询时点活动告警" : "窗口内告警记录"}</b></span>
      <span>查询锚点 <b>${esc(fmtDateTime(data.query_time))}</b></span>
    </div>
    <table class="data-table">
      <thead><tr><th>告警</th><th>设备</th><th>发生时间</th><th>恢复时间</th><th>提醒次数</th></tr></thead>
      <tbody>${episodes
        .map(
          (item) => `<tr>
            <td><b>${esc(item.alarm_name || item.alarm_type_id)}</b><br><span class="mono">${esc(item.episode_id || "")}</span></td>
            <td>${esc(item.asset_name || item.device_id || "—")}</td>
            <td>${esc(fmtDateTime(item.occurred_at))}</td>
            <td>${item.recovered_at ? esc(fmtDateTime(item.recovered_at)) : "未恢复"}</td>
            <td>${esc(item.notification_count ?? 1)}</td>
          </tr>`,
        )
        .join("")}</tbody>
    </table>
    ${episodes.some((item) => item.recovered_at === null)
      ? ""
      : '<div class="task-note">窗口内记录均已恢复；这不等于设备当前正常，实时状态仍需按查询时点重新核查。</div>'}
  `;
}

function renderKnowledge(card) {
  const data = card.data || {};
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const answer = String(data.answer || "知识库没有形成可用回答。");
  const route =
    data.route === "faq"
      ? "FAQ 快速通道"
      : data.route === "sop_first"
        ? "SOP 优先检索"
        : data.route === "manual_first"
          ? "手册优先检索"
          : "SOP / 手册混合检索";
  const sourceList = sources.length
    ? `<div class="sop-list">${sources
        .map(
          (source) => `<div class="sop">
            <div class="sop-head">
              <b>${esc(source.title || source.doc_id)}${source.section ? ` / ${esc(source.section)}` : ""}</b>
              <span>${esc(source.doc_type || "doc")} · ${esc(source.doc_id)} · 版本 ${esc(source.doc_version)}</span>
            </div>
            <p class="mono">chunk ${esc(source.chunk_id)}${source.score !== undefined && source.score !== null ? ` · score ${esc(source.score)}` : ""}</p>
          </div>`,
        )
        .join("")}</div>`
    : '<div class="task-note warn">没有可引用的已审核有效来源，本次不作推测性回答。</div>';
  return `
    <div class="metric-meta">
      <span class="badge ${data.faq_used ? "badge-ok" : "badge-muted"}">${esc(route)}</span>
      <span>来源数 <b>${sources.length}</b></span>
      <span>回答完整性 <b>${card.complete ? "完整" : "缺口"}</b></span>
    </div>
    <div class="answer-text" style="margin-top:12px">${esc(answer)}</div>
    ${section("引用来源", sourceList)}
    <p class="source-note">知识资料只作为检查或技术依据，不直接作为本次设备故障的根因证据。</p>
  `;
}

function analysisFallbackLabel(bundle) {
  const statuses = new Set((bundle.anomaly_assertions || []).map((item) => item.status));
  if (bundle.status === "failed") return "分析未完成";
  if (statuses.has("confirmed_abnormal")) return "发现异常";
  if (statuses.has("confirmed_normal")) return "未发现异常";
  return "无法判定";
}

function renderAnalysisCompact(card) {
  const bundle = card.analysis;
  if (!bundle) {
    return `<div class="task-note danger">分析未形成证据包，不能输出异常判断。${
      card.error_code ? `错误码：${esc(card.error_code)}。` : ""
    }</div>`;
  }
  const conclusion = (card.data && card.data.conclusion) || {};
  const label = conclusion.label || analysisFallbackLabel(bundle);
  const detail = conclusion.detail || "详细结论与核查依据见下方。";
  const tone =
    label === "发现异常" || label === "分析未完成"
      ? "danger"
      : label === "未发现异常"
        ? "ok"
        : "warn";
  const alarmCount = (bundle.facts || []).filter((fact) => fact.kind === "alarm_event").length;
  const gaps = bundle.gaps || [];
  const gapNames = gaps
    .slice(0, 2)
    .map((gap) => gap.item)
    .join("、");
  return `
    <div class="analysis-verdict ${tone}">
      <b>${esc(label)}</b>
      <p>${esc(detail)}</p>
    </div>
    <div class="metric-meta">
      <span>平台告警 <b>${alarmCount}</b> 条</span>
      <span>观测事实 <b>${(bundle.facts || []).length}</b> 项</span>
      <span>数据缺口 <b>${gaps.length}</b> 项</span>
    </div>
    <p class="source-note">${
      gaps.length
        ? `缺口：${esc(gapNames)}${gaps.length > 2 ? " 等" : ""}；`
        : ""
    }详细观测与检查依据见「查看核查依据」。</p>
  `;
}

function renderAnalysis(card) {
  const bundle = card.analysis;
  const data = card.data || {};
  const report = data.report || (card.result && card.result.data && card.result.data.report);
  if (!bundle) {
    return `<div class="task-note danger">分析未形成证据包，不能输出根因判断。${
      card.error_code ? `错误码：${esc(card.error_code)}。` : ""
    }</div>`;
  }
  const assertions = (bundle.anomaly_assertions || [])
    .map((assertion) => {
      const meta = ASSERTION_LABELS[assertion.status] || {
        label: assertion.status,
        cls: "badge-muted",
      };
      return `<div class="assertion">
        <span class="badge ${meta.cls}">${esc(meta.label)}</span>
        <span class="tag">${esc(assertion.source)}</span>
        <span>${esc(assertion.statement || assertion.anomaly_kind || "")}</span>
        ${assertion.evidence_ids && assertion.evidence_ids.length
          ? `<span class="tag">证据 ${assertion.evidence_ids.map(esc).join(", ")}</span>`
          : ""}
      </div>`;
    })
    .join("");
  const hasUnknown = (bundle.anomaly_assertions || []).some(
    (assertion) => assertion.status === "unknown",
  );
  const featureList = (bundle.features || [])
    .map((feature) => {
      const meta = FEATURE_LABELS[feature.status] || { label: feature.status, cls: "badge-muted" };
      return `<div class="feature">
        <div class="feature-head">
          <b>${esc(feature.id)} ${esc(feature.name)}</b>
          <span class="badge ${meta.cls}">${esc(meta.label)}</span>
          ${feature.value !== undefined && feature.value !== null
            ? `<span class="tag">${esc(featValue(feature.value))} ${esc(feature.unit || "")}</span>`
            : ""}
        </div>
        <p>${esc(feature.reason || "没有说明")}</p>
        ${paramChips(feature.parameter_refs)}
        ${feature.evidence_ids && feature.evidence_ids.length
          ? `<div class="param-chips">${feature.evidence_ids
              .map((id) => `<span class="param-chip">证据 ${esc(id)}</span>`)
              .join("")}</div>`
          : ""}
      </div>`;
    })
    .join("");
  const factList = (bundle.facts || [])
    .map(
      (fact) => `<div class="fact ${fact.source_system && fact.source_system.includes("alarm") ? "platform" : ""}">
        <span class="id">${esc(fact.id)}</span>
        <span class="stmt">${esc(fact.statement)}<br>
          <span class="tag">${esc(fact.source_system)} / ${esc(fact.source_record_id)}${
            fact.observed_at ? ` / ${esc(fmtDateTime(fact.observed_at))}` : ""
          }${fact.quality ? ` / 质量 ${esc(fact.quality)}` : ""}${
            fact.coverage ? ` / 覆盖 ${esc(fact.coverage)}` : ""
          }${fact.data && fact.data.length ? ` / ${fact.data.length} 个样本` : ""}</span>
        </span>
      </div>`,
    )
    .join("");
  const hypotheses = (bundle.hypotheses || [])
    .map(
      (hypothesis) => `<div class="hypothesis">
        <b>${esc(hypothesis.label)}</b>
        <div class="param-chips">
          <span class="param-chip">${esc(HYPOTHESIS_ASSESSMENT[hypothesis.assessment] || hypothesis.assessment)}</span>
          <span class="param-chip">${esc(CONFIRMATION_LABELS[hypothesis.confirmation] || hypothesis.confirmation)}</span>
        </div>
        <p>${esc(hypothesis.code)}${
          hypothesis.support_feature_ids && hypothesis.support_feature_ids.length
            ? ` · 支持特征 ${hypothesis.support_feature_ids.map(esc).join(", ")}`
            : ""
        }${
          hypothesis.conflicting_evidence_ids && hypothesis.conflicting_evidence_ids.length
            ? ` · 冲突证据 ${hypothesis.conflicting_evidence_ids.map(esc).join(", ")}`
            : ""
        }</p>
      </div>`,
    )
    .join("");
  const gaps = (bundle.gaps || [])
    .map(
      (gap) => `<div class="gap">
        <b>${esc(GAP_TYPE_LABELS[gap.type] || gap.type)} · ${esc(gap.item)}</b>
        <span>${esc(gap.impact)}${gap.related_step_id ? `（步骤 ${esc(gap.related_step_id)}）` : ""}</span>
      </div>`,
    )
    .join("");
  const sops = (bundle.sop_snippets || [])
    .map(
      (snippet) => `<div class="sop">
        <div class="sop-head">
          <b>${esc(snippet.title)}${snippet.section ? ` / ${esc(snippet.section)}` : ""}</b>
          <span>${esc(snippet.doc_id)} · 版本 ${esc(snippet.doc_version)}</span>
        </div>
        <p>${esc(snippet.text)}</p>
        ${snippet.purpose ? `<p class="source-note">用途：${esc(SOP_PURPOSE_LABELS[snippet.purpose] || snippet.purpose)}</p>` : ""}
      </div>`,
    )
    .join("");
  const claim = bundle.anomaly_claim
    ? `<div class="claim"><span>用户主张（未核实）</span><span>${esc(bundle.anomaly_claim.statement)}</span></div>`
    : "";
  const metadata = kvGrid([
    { k: "分析状态", v: STATUS_LABELS[bundle.status] || bundle.status },
    { k: "配置包", v: bundle.config_bundle_version || "—" },
    { k: "场景", v: `${bundle.scenario_id || "—"}${bundle.scenario_version ? ` / ${bundle.scenario_version}` : ""}` },
    { k: "特征档", v: bundle.feature_profile_version || "—" },
    { k: "快照口径", v: bundle.topology_is_current_snapshot ? "当前拓扑快照（非告警时点）" : "告警时点快照" },
    { k: "兜底场景", v: bundle.fallback_used ? "已使用" : "未使用" },
  ]);
  return `
    ${claim}
    <div class="metric-meta">
      <span>证据包 <b>${esc(bundle.analysis_id)}</b></span>
      <span>状态 <b>${esc(STATUS_LABELS[bundle.status] || bundle.status)}</b></span>
      <span>事实 <b>${(bundle.facts || []).length}</b></span>
      <span>特征 <b>${(bundle.features || []).length}</b></span>
      <span>缺口 <b>${(bundle.gaps || []).length}</b></span>
    </div>
    ${section("异常判断（来源分离）", `${assertions || '<div class="task-note">没有形成异常断言。</div>'}${
      hasUnknown ? '<p class="source-note">存在 unknown 结论：未知不等于正常，不能据此排除异常。</p>' : ""
    }`)}
    ${section("候选排查方向", hypotheses)}
    ${section("后端计算特征", featureList)}
    ${section("外部观测事实", factList)}
    ${section("证据缺口", gaps)}
    ${section("检查依据（SOP / 手册）", sops)}
    ${section("计划版本快照", `${metadata}<p class="source-note">plan_hash：<span class="mono" title="${esc(bundle.plan_hash || "")}">${esc(shortHash(bundle.plan_hash))}</span>；运行任务固定使用该配置包，不随新配置热更新。</p>`)}
    ${report ? `<details class="report"><summary>查看完整分析报告</summary><pre class="report-text">${esc(report)}</pre></details>` : ""}
    <p class="source-note">平台告警、后端计算结果与用户主张分别标注；SOP 只作为检查依据，不直接证明本次故障根因。</p>
  `;
}

function featValue(value) {
  if (typeof value === "number") return fmtNum(value);
  return String(value);
}

export function candidateSecondary(field, candidate) {
  if (field === "device") return `asset_id ${candidate.asset_id || "—"}`;
  if (field === "metric") {
    const unit = candidate.metadata && candidate.metadata.unit ? ` · 单位 ${candidate.metadata.unit}` : "";
    return `metric_code ${candidate.metadata && candidate.metadata.metric_code ? candidate.metadata.metric_code : "—"}${unit}`;
  }
  if (field === "alarm") {
    const kind = candidate.metadata && candidate.metadata.anomaly_kind ? ` · ${candidate.metadata.anomaly_kind}` : "";
    return `alarm_type_id ${candidate.alarm_type_id || "—"}${kind}`;
  }
  return candidate.id;
}

/* ------------------------------------------------------------------ */
/* 会话与查询状态机                                                     */
/* ------------------------------------------------------------------ */

export class Conversation {
  constructor(name = "演示会话") {
    this.name = name;
    this.sessionId = null;
    this.sessionPromise = null;
  }

  async ensureSession() {
    if (this.sessionId) return this.sessionId;
    if (this.sessionPromise) return this.sessionPromise;
    this.sessionPromise = request("/api/v1/sessions", {
      method: "POST",
      body: { name: this.name },
    })
      .then((session) => {
        this.sessionId = session.session_id;
        return this.sessionId;
      })
      .finally(() => {
        this.sessionPromise = null;
      });
    return this.sessionPromise;
  }

  createTurn(query, hooks = {}) {
    return new QueryTurn(this, query, hooks);
  }
}

export class QueryTurn {
  constructor(conversation, query, hooks = {}) {
    this.conversation = conversation;
    this.query = query;
    this.hooks = hooks;
    this.requestId = null;
    this.revision = null;
    this.status = "idle";
    this.answer = "";
    this.tasks = new Map();
    this.confirmations = [];
    this.done = false;
    this.submitting = false;
    this.confirming = false;
    this.feedbackSent = false;
    this.source = null;
    this.seen = new Set();
    this.lastEventId = null;
    this.replayTimer = null;
  }

  emit(type, ...args) {
    const handler = this.hooks[type];
    if (typeof handler === "function") handler(this, ...args);
  }

  setRevision(value) {
    if (typeof value === "number") {
      this.revision = value;
      this.emit("revision", value);
    }
  }

  setStatus(status, text) {
    this.status = status;
    this.emit("status", status, text);
  }

  async start() {
    if (this.submitting) return;
    this.submitting = true;
    this.emit("status", "accepted", "已提交，正在解析意图…");
    try {
      const sessionId = await this.conversation.ensureSession();
      const accepted = await request("/api/v1/queries", {
        method: "POST",
        body: {
          session_id: sessionId,
          query: this.query,
          client_request_id: makeId("client"),
        },
      });
      this.requestId = accepted.request_id;
      this.setRevision(accepted.revision);
      this.setStatus("accepted", "已受理，等待后端解析与计划校验…");
      this.openStream(accepted.events_url);
    } catch (error) {
      this.submitting = false;
      this.done = true;
      this.setStatus("failed", "提交失败");
      this.emit("error", `提交查询失败：${error.message}`);
      this.emit("done", "failed");
    } finally {
      this.submitting = false;
      this.emit("busy", false);
    }
  }

  openStream(url) {
    if (this.source) this.source.close();
    const source = new EventSource(url);
    this.source = source;
    ["accepted", "progress", "confirmation_required", "task_result", "answer_delta", "done"].forEach(
      (type) => source.addEventListener(type, (event) => this.handleSse(type, event)),
    );
    source.addEventListener("error", (event) => this.handleSourceError(event));
  }

  handleSse(type, event) {
    const eventId = event.lastEventId || "";
    if (eventId) {
      if (this.seen.has(eventId)) return;
      this.seen.add(eventId);
      this.lastEventId = eventId;
    }
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      this.emit("error", "收到无法解析的事件数据，已忽略该事件。");
      return;
    }
    if (type === "accepted") {
      this.requestId = data.request_id || this.requestId;
      this.setRevision(data.revision);
      const parser = data.parser === "llm" ? "模型语义解析" : data.parser === "rules" ? "规则解析" : "语义解析";
      this.setStatus("accepted", `已受理 · ${parser}，后端正在校验计划…`);
      (data.tasks || []).forEach((task) => {
        this.tasks.set(task.task_id, task);
        this.emit("task", task);
      });
      return;
    }
    if (type === "progress") {
      if (data.revision) this.setRevision(data.revision);
      const card = data.task_id ? this.tasks.get(data.task_id) : null;
      const title = card ? card.title : data.task_id || "";
      if (data.stage === "task_started") {
        this.setStatus("running", `执行中 · ${title}`);
      } else if (data.stage === "task_finished") {
        const status = data.status || "success";
        this.setStatus("running", `${STATUS_LABELS[status] || status} · ${title}`);
      } else if (data.stage === "task_skipped") {
        this.setStatus("running", `条件跳过 · ${humanWarning(data.reason) || "未满足执行条件"}`);
      } else if (data.stage === "confirmation_resolved") {
        this.setStatus("running", "已确认对象，继续执行后端取证计划…");
      } else if (data.stage === "cancelled") {
        this.setStatus("cancelled", "查询已取消");
      }
      this.emit("progress", data);
      return;
    }
    if (type === "confirmation_required") {
      this.setRevision(data.revision);
      this.requestId = data.request_id || this.requestId;
      this.confirmations = data.confirmations || [];
      this.setStatus("waiting_user", "等待确认查询对象，系统不会猜测设备或测点");
      this.emit("confirmation", this.confirmations);
      return;
    }
    if (type === "task_result") {
      this.tasks.set(data.task_id, data);
      this.emit("task", data);
      return;
    }
    if (type === "answer_delta") {
      this.answer += data.delta || "";
      this.emit("answer", true);
      return;
    }
    if (type === "done") {
      this.finalize(data.overall_status || "success");
    }
  }

  handleSourceError(event) {
    if (event && typeof event.data === "string") {
      const eventId = event.lastEventId || "";
      if (eventId) {
        if (this.seen.has(eventId)) return;
        this.seen.add(eventId);
      }
      let payload = {};
      try {
        payload = JSON.parse(event.data);
      } catch {
        payload = { message: event.data };
      }
      this.emit("error", `查询未完成：${payload.message || payload.code || "服务返回错误"}`);
      return;
    }
    if (this.done) return;
    if (!this.source) return;
    if (this.source.readyState === EventSource.CLOSED) {
      this.setStatus("partial", "事件流连接已断开，正在尝试恢复…");
    } else {
      this.setStatus("partial", "连接中断，浏览器正在自动重连…");
    }
    if (this.replayTimer) clearTimeout(this.replayTimer);
    this.replayTimer = setTimeout(() => {
      this.replayTimer = null;
      if (!this.done) this.syncSnapshot().catch(() => {});
    }, 1500);
  }

  async syncSnapshot() {
    if (!this.requestId) return null;
    const snapshot = await request(`/api/v1/queries/${this.requestId}`);
    this.applySnapshot(snapshot);
    return snapshot;
  }

  applySnapshot(snapshot) {
    if (!snapshot) return;
    this.setRevision(snapshot.revision);
    (snapshot.tasks || []).forEach((task) => {
      this.tasks.set(task.task_id, task);
      this.emit("task", task);
    });
    if (snapshot.answer) {
      this.answer = snapshot.answer;
      this.emit("answer", false);
    }
    if ((snapshot.pending_confirmations || []).length) {
      this.confirmations = snapshot.pending_confirmations;
      this.emit("confirmation", this.confirmations);
    } else {
      this.confirmations = [];
      this.emit("confirmation", this.confirmations);
    }
    if (["success", "partial", "failed", "cancelled"].includes(snapshot.overall_status)) {
      this.finalize(snapshot.overall_status);
    } else if (
      snapshot.overall_status === "waiting_user" &&
      !(snapshot.pending_confirmations || []).length
    ) {
      this.finalize("waiting_user");
    }
  }

  finalize(status) {
    const firstFinalize = !this.done;
    this.done = true;
    this.submitting = false;
    this.status = status;
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    if (this.replayTimer) {
      clearTimeout(this.replayTimer);
      this.replayTimer = null;
    }
    if (!this.answer && status !== "waiting_user") {
      this.answer = "没有可展示的查询结果。";
      this.emit("answer", false);
    }
    this.setStatus(status, OVERALL_LABELS[status] || status);
    if (firstFinalize) {
      this.emit("done", status);
      this.syncSnapshot().catch(() => {});
    }
  }

  async chooseCandidate(confirmationId, selectedId) {
    if (this.confirming) return;
    this.confirming = true;
    this.emit("confirming", true);
    try {
      const accepted = await request(`/api/v1/confirmations/${encodeURIComponent(confirmationId)}`, {
        method: "POST",
        body: {
          selected_id: selectedId,
          session_revision: this.revision,
          client_request_id: makeId("confirm"),
        },
      });
      this.setRevision(accepted.revision);
      this.confirmations = [];
      this.emit("confirmation", []);
      this.setStatus("running", "已确认对象，继续执行后端取证计划…");
      if (!this.source) {
        this.done = false;
        this.openStream(accepted.events_url);
      }
      this.syncSnapshot().catch(() => {});
    } catch (error) {
      this.emit("error", `确认失败：${error.message}`);
      if (error.status === 409 || error.status === 404) {
        this.syncSnapshot().catch(() => {});
      }
    } finally {
      this.confirming = false;
      this.emit("confirming", false);
    }
  }

  async cancel() {
    if (!this.requestId || this.done) return;
    try {
      const snapshot = await request(`/api/v1/queries/${this.requestId}/cancel`, {
        method: "POST",
        body: { reason: "user_cancelled" },
      });
      this.applySnapshot(snapshot);
    } catch (error) {
      this.emit("error", `取消失败：${error.message}`);
    }
  }

  dispose() {
    if (this.source) {
      this.source.close();
      this.source = null;
    }
    if (this.replayTimer) {
      clearTimeout(this.replayTimer);
      this.replayTimer = null;
    }
  }
}
