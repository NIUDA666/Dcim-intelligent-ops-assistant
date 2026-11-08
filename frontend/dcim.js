/* 模拟 DCIM 宿主页：平面图、告警入口，以及复用 core.js 的悬浮助手窗口。 */

import {
  Conversation,
  OVERALL_LABELS,
  candidateSecondary,
  esc,
  fmtDateTime,
  renderAnalysisTrend,
  renderTaskCard,
  renderTrendVisual,
  renderRichText,
  request,
  statusClass,
  submitFeedback,
} from "./core.js";

const $ = (selector, root = document) => root.querySelector(selector);

/* ------------------------------------------------------------------ */
/* 模拟宿主数据（与 fixtures 保持一致，仅用于界面展示）                 */
/* ------------------------------------------------------------------ */

const DEVICES = {
  asset_ac001: {
    assetId: "asset_ac001",
    code: "AC001",
    name: "精密空调 AC001",
    room: "一号机房",
    roomId: "room_01",
    metric: "送风温度",
    value: "29.1",
    unit: "℃",
    status: "warn",
    state: "告警",
    query: "一号机房AC001送风温度最近两小时趋势，如果有异常再分析",
  },
  asset_ac002: {
    assetId: "asset_ac002",
    code: "AC002",
    name: "精密空调 AC002",
    room: "一号机房",
    roomId: "room_01",
    metric: "送风温度",
    value: "24.0",
    unit: "℃",
    status: "ok",
    state: "正常",
    query: "一号机房AC002送风温度最近一小时趋势",
  },
  asset_ac003: {
    assetId: "asset_ac003",
    code: "AC003",
    name: "精密空调 AC003",
    room: "一号机房",
    roomId: "room_01",
    metric: "送风温度",
    value: "24.0",
    unit: "℃",
    status: "ok",
    state: "正常",
    query: "一号机房AC003送风温度现在多少",
  },
  asset_ac001_r2: {
    assetId: "asset_ac001_r2",
    code: "AC001",
    name: "精密空调 AC001（二号机房）",
    room: "二号机房",
    roomId: "room_02",
    metric: "送风温度",
    value: "24.0",
    unit: "℃",
    status: "ok",
    state: "正常",
    query: "二号机房AC001送风温度最近一小时趋势",
  },
  asset_ac004: {
    assetId: "asset_ac004",
    code: "AC004",
    name: "精密空调 AC004",
    room: "二号机房",
    roomId: "room_02",
    metric: "送风温度",
    value: "24.0",
    unit: "℃",
    status: "ok",
    state: "正常",
    query: "二号机房AC004送风温度现在多少",
  },
  asset_chiller_01: {
    assetId: "asset_chiller_01",
    code: "CH001",
    name: "冷水机组 CH001",
    room: "冷源机房",
    roomId: "room_00",
    metric: "冷冻水供水温度",
    value: "6.97",
    unit: "℃",
    status: "ok",
    state: "运行",
    query: "冷水机组CH001冷冻水供水温度现在多少",
  },
  asset_power_01: {
    assetId: "asset_power_01",
    code: "PD001",
    name: "配电柜 PD001",
    room: "配电间",
    roomId: "room_00",
    metric: "运行状态",
    value: "ON",
    unit: "",
    status: "ok",
    state: "运行",
    query: "配电柜PD001运行状态",
  },
};

const ALARMS = [
  {
    severity: "急",
    title: "送风温度过高",
    deviceId: "asset_ac001",
    device: "精密空调 AC001",
    room: "一号机房",
    when: "10:10 发生",
    state: "未恢复",
    active: true,
    query: "梳理一下今天一号机房AC001高温告警发生了几次，现在还有几条未恢复",
  },
  {
    severity: "告",
    title: "送风温度过高",
    deviceId: "asset_ac001",
    device: "精密空调 AC001",
    room: "一号机房",
    when: "09:40 发生 · 09:50 恢复",
    state: "已恢复",
    active: false,
    query: "梳理一下今天一号机房AC001高温告警发生了几次，现在还有几条未恢复",
  },
  {
    severity: "告",
    title: "送风温度过高",
    deviceId: "asset_ac002",
    device: "精密空调 AC002",
    room: "一号机房",
    when: "09:00 发生 · 09:20 恢复",
    state: "已恢复",
    active: false,
    query: "梳理一下今天一号机房AC002高温告警发生了几次",
  },
];

const ROOM_01 = {
  racksA: [
    { code: "R01", load: 62 },
    { code: "R02", load: 58 },
    { code: "R03", load: 71 },
    { code: "R04", load: 46 },
    { code: "R05", load: 55 },
    { code: "R06", load: 64 },
  ],
  racksB: [
    { code: "R07", load: 49 },
    { code: "R08", load: 53 },
    { code: "R09", load: 66 },
    { code: "R10", load: 44 },
    { code: "R11", load: 57 },
    { code: "R12", load: 61 },
  ],
  devices: ["asset_ac001", "asset_ac002", "asset_ac003"],
};

const ROOM_02 = {
  racksA: [
    { code: "R21", load: 38 },
    { code: "R22", load: 42 },
    { code: "R23", load: 35 },
  ],
  racksB: [
    { code: "R24", load: 47 },
    { code: "R25", load: 39 },
    { code: "R26", load: 33 },
  ],
  devices: ["asset_ac001_r2", "asset_ac004"],
};

const drawer = $("#assistant-drawer");
const drawerHead = $(".drawer-head", drawer);
const fab = $("#assistant-fab");
const scrim = $("#drawer-scrim");
const thread = $("#chat-thread");
const welcome = $("#chat-welcome");
const chatScroll = $("#chat-scroll");
const input = $("#chat-input");
const sendBtn = $("#chat-send");
const contextChip = $("#context-chip");
const contextText = $("#context-text");
const reportModal = $("#report-modal");
const reportModalTitle = $("#report-modal-title");
const reportModalBody = $("#report-modal-body");

let conversation = new Conversation("DCIM 值班会话");
let activeTurn = null;
let busy = false;
let context = null;
let toastTimer = null;

function deviceTile(device) {
  return `<button type="button" class="device-tile ${device.status}" data-device="${esc(device.assetId)}" title="${esc(device.name)} · ${esc(device.room)}">
    <span class="dev-top"><i class="dev-dot"></i><b>${esc(device.code)}</b><span class="dev-state">${esc(device.state)}</span></span>
    <span class="dev-metric">${esc(device.value)}${device.unit ? `<small>${esc(device.unit)}</small>` : ""}</span>
    <span class="dev-sub">${esc(device.metric)}</span>
  </button>`;
}

function rackRow(label, racks) {
  return `<div class="rack-row">
    <span class="row-tag">${esc(label)}</span>
    <div class="rack-grid">${racks
      .map(
        (rack) =>
          `<div class="rack" style="--load:${rack.load}%"><b>${esc(rack.code)}</b><small>${rack.load}%</small></div>`,
      )
      .join("")}</div>
  </div>`;
}

function cracRow(devices) {
  return `<div class="crac-row">${devices.map((id) => deviceTile(DEVICES[id])).join("")}</div>`;
}

function renderHost() {
  $("#room-01").innerHTML = `
    ${rackRow("A 列", ROOM_01.racksA)}
    <div class="cold-aisle">冷通道</div>
    ${rackRow("B 列", ROOM_01.racksB)}
    ${cracRow(ROOM_01.devices)}
  `;
  $("#room-02").innerHTML = `
    ${rackRow("A 列", ROOM_02.racksA)}
    <div class="cold-aisle">冷通道</div>
    ${rackRow("B 列", ROOM_02.racksB)}
    ${cracRow(ROOM_02.devices)}
  `;
  $("#alarm-list").innerHTML = ALARMS.map(
    (alarm, index) => `<button type="button" class="alarm-item ${alarm.active ? "active" : "recovered"}" data-alarm="${index}">
      <span class="alarm-sev">${esc(alarm.severity)}</span>
      <span class="alarm-main">
        <b>${esc(alarm.title)}</b>
        <p>${esc(alarm.device)} · ${esc(alarm.room)}</p>
        <span class="alarm-meta"><span>${esc(alarm.when)}</span><span class="state">${esc(alarm.state)}</span></span>
      </span>
    </button>`,
  ).join("");
  $("#support-devices").innerHTML = ["asset_chiller_01", "asset_power_01"]
    .map((id) => {
      const device = DEVICES[id];
      return `<button type="button" class="support-item" data-device="${esc(device.assetId)}">
        <span class="support-icon">${esc(device.code.slice(0, 2))}</span>
        <span class="support-main"><b>${esc(device.name)}</b><span>${esc(device.room)} · ${esc(device.metric)}</span></span>
        <span class="support-value">${esc(device.value)}${device.unit ? `<small>${esc(device.unit)}</small>` : ""}</span>
      </button>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* 宿主交互                                                             */
/* ------------------------------------------------------------------ */

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 2600);
}

function isNarrow() {
  return window.matchMedia("(max-width: 860px)").matches;
}

function openDrawer(focus = false) {
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  fab.classList.add("hidden");
  fitDrawerToViewport();
  if (isNarrow()) scrim.hidden = false;
  if (focus) setTimeout(() => input.focus({ preventScroll: true }), 280);
}

function closeDrawer() {
  dragState = null;
  drawer.classList.remove("dragging");
  drawer.style.removeProperty("transform");
  if (drawer.contains(document.activeElement)) {
    fab.focus({ preventScroll: true });
  }
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  fab.classList.remove("hidden");
  scrim.hidden = true;
}

function openReportModal(report) {
  reportModalTitle.textContent = report.title ? `${report.title} · 核查依据` : "核查依据";
  reportModalBody.innerHTML = renderRichText(report.markdown);
  reportModalBody.scrollTop = 0;
  reportModal.hidden = false;
  $("#report-modal-close").focus({ preventScroll: true });
}

function closeReportModal() {
  reportModal.hidden = true;
  reportModalBody.innerHTML = "";
}

/* 悬浮窗拖动：标题栏按住移动，限制在视口内；双击标题栏复位。 */
const DRAG_MARGIN = 8;
let dragState = null;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function resetDrawerPosition() {
  dragState = null;
  drawer.classList.remove("dragging");
  ["left", "top", "right", "bottom", "width", "height", "transform"].forEach((property) => {
    drawer.style.removeProperty(property);
  });
}

function fitDrawerToViewport() {
  if (window.innerWidth <= 640) {
    resetDrawerPosition();
    return;
  }
  if (!drawer.classList.contains("open") || !drawer.style.left) return;
  const rect = drawer.getBoundingClientRect();
  const width = Math.min(rect.width, window.innerWidth - DRAG_MARGIN * 2);
  const height = Math.min(rect.height, window.innerHeight - DRAG_MARGIN * 2);
  drawer.style.width = `${width}px`;
  drawer.style.height = `${height}px`;
  drawer.style.left = `${clamp(rect.left, DRAG_MARGIN, window.innerWidth - width - DRAG_MARGIN)}px`;
  drawer.style.top = `${clamp(rect.top, DRAG_MARGIN, window.innerHeight - height - DRAG_MARGIN)}px`;
}

function beginDrawerDrag(event) {
  if (event.button !== 0 || window.innerWidth <= 640) return;
  if (event.target.closest("button, a, input, textarea, select")) return;
  const rect = drawer.getBoundingClientRect();
  dragState = {
    pointerId: event.pointerId,
    offsetX: event.clientX - rect.left,
    offsetY: event.clientY - rect.top,
    width: rect.width,
    height: rect.height,
  };
  drawer.style.left = `${rect.left}px`;
  drawer.style.top = `${rect.top}px`;
  drawer.style.right = "auto";
  drawer.style.bottom = "auto";
  drawer.style.width = `${rect.width}px`;
  drawer.style.height = `${rect.height}px`;
  drawer.style.transform = "none";
  drawer.classList.add("dragging");
  drawer.setPointerCapture(event.pointerId);
  event.preventDefault();
}

function moveDrawerDrag(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;
  const maxLeft = Math.max(DRAG_MARGIN, window.innerWidth - dragState.width - DRAG_MARGIN);
  const maxTop = Math.max(DRAG_MARGIN, window.innerHeight - dragState.height - DRAG_MARGIN);
  drawer.style.left = `${clamp(event.clientX - dragState.offsetX, DRAG_MARGIN, maxLeft)}px`;
  drawer.style.top = `${clamp(event.clientY - dragState.offsetY, DRAG_MARGIN, maxTop)}px`;
}

function endDrawerDrag(event) {
  if (!dragState || event.pointerId !== dragState.pointerId) return;
  dragState = null;
  drawer.classList.remove("dragging");
  drawer.style.removeProperty("transform");
  if (drawer.hasPointerCapture(event.pointerId)) {
    drawer.releasePointerCapture(event.pointerId);
  }
}

function setContext(next) {
  context = next;
  if (!next) {
    contextChip.hidden = true;
    contextText.textContent = "";
    return;
  }
  contextChip.hidden = false;
  contextText.textContent = next.label;
}

function composeQuery(query) {
  if (!context) return query;
  if (query.includes(context.code)) return query;
  return `${context.prefix}${query}`;
}

function pickDevice(device) {
  document.querySelectorAll(".device-tile.selected").forEach((node) => node.classList.remove("selected"));
  document.querySelectorAll(`[data-device="${device.assetId}"]`).forEach((node) => node.classList.add("selected"));
  setContext({
    label: `${device.room} · ${device.code} · asset_id ${device.assetId}`,
    code: device.code,
    prefix: `${device.room}${device.code}，`,
  });
  input.value = device.query;
  autoGrow();
  openDrawer(true);
  toast(`已带入 ${device.room} ${device.code}，可直接提问`);
}

function pickAlarm(alarm) {
  const device = DEVICES[alarm.deviceId];
  setContext({
    label: `${alarm.room} · ${device.code} · 今日告警窗口`,
    code: device.code,
    prefix: `${alarm.room}${device.code}高温告警，`,
  });
  input.value = alarm.query;
  autoGrow();
  openDrawer(true);
  toast("已带入告警设备与时间窗");
}

/* ------------------------------------------------------------------ */
/* 助手对话                                                             */
/* ------------------------------------------------------------------ */

function autoGrow() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
}

function scrollBottom(force = false) {
  const nearBottom = chatScroll.scrollHeight - chatScroll.scrollTop - chatScroll.clientHeight < 140;
  if (force || nearBottom) {
    chatScroll.scrollTo({ top: chatScroll.scrollHeight, behavior: force ? "smooth" : "auto" });
  }
}

function setBusy(value) {
  busy = value;
  sendBtn.classList.toggle("stop", value);
  sendBtn.querySelector(".send-label").textContent = value ? "停止" : "发送";
  sendBtn.setAttribute("aria-label", value ? "停止本次查询" : "发送");
}

function appendUserMessage(text) {
  const message = document.createElement("div");
  message.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  message.appendChild(bubble);
  thread.appendChild(message);
}

function buildTurnUi() {
  const element = document.createElement("div");
  element.className = "msg assistant";
  element.innerHTML = `
    <span class="msg-avatar">AI</span>
    <div class="msg-content">
      <div class="turn-status"><span class="status-dot accepted"></span><span class="turn-status-text">已提交，正在解析意图…</span></div>
      <details class="trace" open>
        <summary>取证过程<span class="trace-count">准备中</span></summary>
        <div class="trace-list"></div>
      </details>
      <div class="turn-confirm" hidden></div>
      <div class="turn-answer" hidden></div>
      <div class="turn-visuals" hidden></div>
      <div class="turn-actions"></div>
    </div>`;
  return {
    element,
    statusDot: $(".status-dot", element),
    statusText: $(".turn-status-text", element),
    trace: $(".trace", element),
    traceList: $(".trace-list", element),
    traceCount: $(".trace-count", element),
    confirm: $(".turn-confirm", element),
    answer: $(".turn-answer", element),
    visuals: $(".turn-visuals", element),
    actions: $(".turn-actions", element),
    taskNodes: new Map(),
    visualNodes: new Map(),
    briefs: new Map(),
    errorNode: null,
  };
}

function updateTraceCount(ui) {
  const count = ui.taskNodes.size;
  ui.traceCount.textContent = count ? `${count} 个子任务` : "准备中";
}

function turnActions(ui, turn) {
  ui.actions.innerHTML = "";
  const addButton = (label, handler, disabled = false) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mini-btn";
    button.textContent = label;
    button.disabled = disabled;
    button.addEventListener("click", handler);
    ui.actions.appendChild(button);
    return button;
  };
  ui.briefs.forEach((brief) => {
    addButton("查看核查依据", () => openReportModal(brief));
  });
  if (turn.answer) {
    addButton("复制回答", async () => {
      try {
        await navigator.clipboard.writeText(turn.answer);
      } catch {
        const helper = document.createElement("textarea");
        helper.value = turn.answer;
        document.body.appendChild(helper);
        helper.select();
        document.execCommand("copy");
        helper.remove();
      }
      toast("回答已复制");
    });
  }
  addButton("重试", () => {
    if (busy) return;
    sendMessage(turn.query);
  });
  if (turn.requestId) {
    addButton("有帮助", () => recordFeedback(ui, turn, "有帮助"));
    addButton("需改进", () => recordFeedback(ui, turn, "需改进"));
  }
  const note = document.createElement("span");
  note.className = "mini-note";
  ui.actions.appendChild(note);
}

async function recordFeedback(ui, turn, label) {
  if (turn.feedbackSent) return;
  turn.feedbackSent = true;
  try {
    await submitFeedback(turn.requestId, label);
    const note = $(".mini-note", ui.actions);
    if (note) note.textContent = "已记录反馈";
  } catch (error) {
    turn.feedbackSent = false;
    toast(`反馈提交失败：${error.message}`);
  }
}

function renderConfirmations(ui, turn, confirmations) {
  if (!confirmations.length) {
    ui.confirm.hidden = true;
    ui.confirm.innerHTML = "";
    return;
  }
  ui.confirm.hidden = false;
  const prompt =
    confirmations.length === 1
      ? confirmations[0].prompt
      : `共有 ${confirmations.length} 个对象需要确认，系统不会猜测设备、测点或告警类型。`;
  ui.confirm.innerHTML = `
    <div class="confirm-title">${esc(prompt)}</div>
    <div class="candidates">${confirmations
      .map((confirmation) =>
        (confirmation.candidates || [])
          .map(
            (candidate) => `<button type="button" class="candidate" data-confirmation="${esc(
              confirmation.confirmation_id,
            )}" data-selected="${esc(candidate.id)}">
              <b>${esc(candidate.label)}</b>
              <span>${esc(candidateSecondary(confirmation.field, candidate))}</span>
            </button>`,
          )
          .join(""),
      )
      .join("")}</div>
    <p class="confirm-note">确认项有效至 ${esc(fmtDateTime(confirmations[0].expires_at))}；选择后按新版本重新编译计划。</p>
  `;
  ui.confirm.querySelectorAll(".candidate").forEach((button) => {
    button.addEventListener("click", () => {
      if (!turn || turn.confirming) return;
      ui.confirm.querySelectorAll("button").forEach((item) => {
        item.disabled = true;
      });
      turn.chooseCandidate(button.dataset.confirmation, button.dataset.selected);
    });
  });
  scrollBottom(true);
}

function turnHooks(ui) {
  return {
    status(turn, status, text) {
      ui.statusDot.className = `status-dot ${statusClass(status)}`;
      ui.statusText.textContent = text;
      if (["success", "partial", "failed", "cancelled"].includes(status)) {
        setBusy(false);
      }
      scrollBottom();
    },
    task(turn, card) {
      const node = renderTaskCard(card, { compact: true });
      const existing = ui.taskNodes.get(card.task_id);
      if (existing) existing.replaceWith(node);
      else ui.traceList.appendChild(node);
      ui.taskNodes.set(card.task_id, node);

      if (card.intent === "metric_trend") {
        const visual = renderTrendVisual(card);
        if (visual) {
          ui.visualNodes.delete("analysis");
          ui.visualNodes.set("trend", visual);
        }
      } else if (card.intent === "alarm_analysis" && !ui.visualNodes.has("trend")) {
        const visual = renderAnalysisTrend(card);
        if (visual) ui.visualNodes.set("analysis", visual);
      }
      if (ui.visualNodes.size) {
        ui.visuals.innerHTML = Array.from(ui.visualNodes.values()).join("");
        ui.visuals.hidden = false;
      }

      const brief = card.intent === "alarm_analysis" && card.result?.data?.brief;
      if (brief) {
        ui.briefs.set(card.task_id, { title: card.title, markdown: brief });
      }
      updateTraceCount(ui);
      scrollBottom();
    },
    answer(turn, streaming) {
      if (turn.answer) {
        ui.answer.hidden = false;
        if (streaming && !turn.done) {
          ui.answer.classList.remove("is-rich");
          ui.answer.textContent = turn.answer;
          ui.answer.classList.add("streaming");
        } else {
          ui.answer.classList.remove("streaming");
          ui.answer.classList.add("is-rich");
          ui.answer.innerHTML = renderRichText(turn.answer);
        }
      } else {
        ui.answer.hidden = true;
      }
      scrollBottom();
    },
    confirmation(turn, confirmations) {
      renderConfirmations(ui, turn, confirmations);
      if (confirmations.length) scrollBottom(true);
    },
    done(turn, status) {
      const hasConfirmations = (turn.confirmations || []).length > 0;
      setBusy(status === "waiting_user" && hasConfirmations);
      ui.statusDot.className = `status-dot ${statusClass(status)}`;
      ui.statusText.textContent =
        status === "waiting_user" && hasConfirmations
          ? "等待确认查询对象"
          : OVERALL_LABELS[status] || status;
      if (turn.answer) {
        ui.answer.hidden = false;
        ui.answer.classList.remove("streaming");
        ui.answer.classList.add("is-rich");
        ui.answer.innerHTML = renderRichText(turn.answer);
      }
      if (ui.taskNodes.size) {
        ui.trace.open = false;
      }
      turnActions(ui, turn);
      scrollBottom();
    },
    error(turn, message) {
      if (!ui.errorNode) {
        ui.errorNode = document.createElement("div");
        ui.errorNode.className = "task-note danger";
        ui.answer.before(ui.errorNode);
      }
      ui.errorNode.textContent = message;
      toast(message);
      scrollBottom();
    },
    busy() {},
    confirming(turn, value) {
      ui.confirm.querySelectorAll("button").forEach((item) => {
        item.disabled = value;
      });
    },
  };
}

function sendMessage(raw) {
  const text = String(raw || "").trim();
  if (!text || busy) return;
  if (activeTurn && !activeTurn.done) return;
  const query = composeQuery(text);
  welcome.hidden = true;
  appendUserMessage(query);
  setContext(null);
  input.value = "";
  autoGrow();

  const ui = buildTurnUi();
  thread.appendChild(ui.element);
  const turn = conversation.createTurn(query, turnHooks(ui));
  activeTurn = turn;
  setBusy(true);
  scrollBottom(true);
  turn.start();
}

function newConversation() {
  if (activeTurn && !activeTurn.done) {
    activeTurn.dispose();
  }
  conversation = new Conversation("DCIM 值班会话");
  activeTurn = null;
  thread.innerHTML = "";
  welcome.hidden = false;
  setContext(null);
  input.value = "";
  autoGrow();
  setBusy(false);
  toast("已新建会话");
  input.focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ */
/* 服务状态                                                             */
/* ------------------------------------------------------------------ */

async function loadHealth() {
  try {
    const health = await request("/api/v1/health");
    const anchor = fmtDateTime(health.fixed_query_time, false);
    $("#dcim-clock").textContent = anchor;
    $("#host-anchor").textContent = anchor;
    $("#drawer-anchor").textContent = anchor;
    $("#drawer-anchor").title = `${health.timezone} · 固定查询锚点`;
    $("#side-bundle").textContent = `配置包 ${health.config_bundle_version || "—"}`;
  } catch {
    toast("后端服务未连接，界面展示静态模拟快照");
  }
}

/* ------------------------------------------------------------------ */
/* 事件绑定与初始化                                                     */
/* ------------------------------------------------------------------ */

function bindEvents() {
  document.addEventListener("click", (event) => {
    const deviceNode = event.target.closest("[data-device]");
    if (deviceNode) {
      const device = DEVICES[deviceNode.dataset.device];
      if (device) pickDevice(device);
      return;
    }
    const alarmNode = event.target.closest("[data-alarm]");
    if (alarmNode) {
      const alarm = ALARMS[Number(alarmNode.dataset.alarm)];
      if (alarm) pickAlarm(alarm);
      return;
    }
    const suggest = event.target.closest(".suggest");
    if (suggest) {
      input.value = suggest.dataset.query || "";
      autoGrow();
      input.focus({ preventScroll: true });
      return;
    }
    const nav = event.target.closest("[data-nav]");
    if (nav && !nav.classList.contains("active")) {
      toast(`演示环境仅开放「机房视图」，${nav.dataset.nav}未接入`);
    }
  });

  $("#assistant-fab").addEventListener("click", () => openDrawer(true));
  $("#promo-open").addEventListener("click", () => openDrawer(true));
  $("#open-assistant-btn").addEventListener("click", () => openDrawer(true));
  $("#close-drawer-btn").addEventListener("click", closeDrawer);
  drawerHead.addEventListener("pointerdown", beginDrawerDrag);
  drawer.addEventListener("pointermove", moveDrawerDrag);
  drawer.addEventListener("pointerup", endDrawerDrag);
  drawer.addEventListener("pointercancel", endDrawerDrag);
  drawerHead.addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) resetDrawerPosition();
  });
  $("#report-modal-close").addEventListener("click", closeReportModal);
  reportModal.addEventListener("click", (event) => {
    if (event.target === reportModal) closeReportModal();
  });
  scrim.addEventListener("click", closeDrawer);
  $("#new-chat-btn").addEventListener("click", newConversation);
  $("#context-close").addEventListener("click", () => setContext(null));
  $("#help-btn").addEventListener("click", () => toast("点击设备或告警可带入上下文；也可以直接输入问题。"));
  $("#refresh-btn").addEventListener("click", (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "刷新中…";
    setTimeout(() => {
      button.disabled = false;
      button.textContent = "刷新快照";
      toast("快照已刷新（演示环境使用固定查询锚点）");
    }, 500);
  });

  sendBtn.addEventListener("click", () => {
    if (busy && activeTurn) {
      activeTurn.cancel();
      return;
    }
    sendMessage(input.value);
  });

  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(input.value);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!reportModal.hidden) {
      closeReportModal();
      return;
    }
    if (drawer.classList.contains("open")) closeDrawer();
  });

  window.addEventListener("resize", () => {
    if (!isNarrow()) scrim.hidden = true;
    fitDrawerToViewport();
  });
}

function init() {
  renderHost();
  bindEvents();
  loadHealth();
  conversation.ensureSession().catch(() => {
    /* 提交时会再次尝试，避免首屏弹错 */
  });
  if (window.matchMedia("(min-width: 1200px)").matches) {
    setTimeout(() => openDrawer(false), 550);
  }
}

init();
