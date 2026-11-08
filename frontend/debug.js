/* 调试控制台：保留全页取证视图，用于验收后端结果与事件顺序。 */

import {
  Conversation,
  OVERALL_LABELS,
  answerPillClass,
  candidateSecondary,
  esc,
  fmtDateTime,
  humanWarning,
  renderRichText,
  renderTaskCard,
  request,
  statusClass,
  submitFeedback,
} from "./core.js";

const $ = (selector, root = document) => root.querySelector(selector);

const el = {
  form: $("#query-form"),
  input: $("#query-input"),
  submit: $("#submit-btn"),
  examples: $("#examples"),
  statusStrip: $("#status-strip"),
  statusDot: $("#status-dot"),
  statusText: $("#status-text"),
  requestId: $("#request-id"),
  revision: $("#revision"),
  cancel: $("#cancel-btn"),
  error: $("#error-banner"),
  confirmationPanel: $("#confirmation-panel"),
  confirmationPrompt: $("#confirmation-prompt"),
  confirmationCandidates: $("#confirmation-candidates"),
  tasks: $("#tasks"),
  answerPanel: $("#answer-panel"),
  answerText: $("#answer-text"),
  answerStatus: $("#answer-status"),
  fixedTime: $("#fixed-time"),
  bundleVersion: $("#bundle-version"),
  sourcePill: $("#source-pill"),
  mockBadge: $(".badge-mock"),
  feedback: $("#feedback-row"),
};

const conversation = new Conversation("调试控制台会话");
let turn = null;
let submitting = false;

function showError(message) {
  el.error.hidden = false;
  el.error.textContent = message;
}

function clearError() {
  el.error.hidden = true;
  el.error.textContent = "";
}

function syncControls() {
  const active = turn && !turn.done && ["accepted", "running", "waiting_user"].includes(turn.status);
  const busy = submitting || active;
  el.submit.disabled = busy;
  el.submit.textContent = submitting ? "提交中…" : active ? "查询进行中…" : "开始查询";
}

function setRevision(value) {
  el.revision.textContent = typeof value === "number" ? String(value) : "—";
}

function updateAnswerPanel(status) {
  if (!turn) return;
  el.answerText.classList.add("is-rich");
  el.answerText.innerHTML = renderRichText(turn.answer || "正在整理回答…");
  el.answerStatus.textContent =
    status === "running" || status === "accepted" ? "生成中" : OVERALL_LABELS[status] || status;
  el.answerStatus.className = answerPillClass(status);
  el.answerPanel.hidden = false;
}

function renderConfirmations(confirmations) {
  if (!confirmations.length) {
    el.confirmationPanel.hidden = true;
    el.confirmationCandidates.innerHTML = "";
    return;
  }
  el.confirmationPanel.hidden = false;
  el.confirmationPrompt.textContent =
    confirmations.length === 1
      ? confirmations[0].prompt
      : `共有 ${confirmations.length} 个对象需要确认，系统不会猜测设备、测点或告警类型。`;
  el.confirmationCandidates.innerHTML = confirmations
    .map(
      (confirmation) => `<div class="confirm-block">
        ${confirmations.length > 1 ? `<div class="confirm-question">${esc(confirmation.prompt)}</div>` : ""}
        <div class="candidates">${(confirmation.candidates || [])
          .map(
            (candidate) => `<button type="button" class="candidate" data-confirmation="${esc(
              confirmation.confirmation_id,
            )}" data-selected="${esc(candidate.id)}">
              <b>${esc(candidate.label)}</b>
              <span>${esc(candidateSecondary(confirmation.field, candidate))}</span>
            </button>`,
          )
          .join("")}</div>
        <p class="source-note">确认项有效至 ${esc(fmtDateTime(confirmation.expires_at))}；选择后按新版本重新编译计划。</p>
      </div>`,
    )
    .join("");
}

function ensureFeedbackRow() {
  if (!turn || !turn.requestId) return;
  let row = $("#feedback-row");
  if (!row) {
    row = document.createElement("div");
    row.id = "feedback-row";
    row.className = "feedback-row";
    row.innerHTML = `
      <span class="feedback-note">这条回答是否有帮助？</span>
      <button type="button" class="ghost small" data-feedback="有帮助">有帮助</button>
      <button type="button" class="ghost small" data-feedback="需改进">需改进</button>
      <span class="feedback-note" id="feedback-status"></span>
    `;
    row.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-feedback]");
      if (!button || !turn || turn.feedbackSent) return;
      turn.feedbackSent = true;
      row.querySelectorAll("button").forEach((item) => {
        item.disabled = true;
      });
      try {
        await submitFeedback(turn.requestId, button.getAttribute("data-feedback"));
        const status = $("#feedback-status");
        if (status) status.textContent = "已记录，感谢反馈。";
      } catch (error) {
        turn.feedbackSent = false;
        row.querySelectorAll("button").forEach((item) => {
          item.disabled = false;
        });
        showError(`反馈提交失败：${error.message}`);
      }
    });
    el.answerPanel.appendChild(row);
  }
}

function resetView() {
  el.tasks.innerHTML = "";
  el.answerPanel.hidden = true;
  el.answerText.textContent = "";
  el.answerStatus.textContent = "—";
  el.answerStatus.className = "pill";
  el.confirmationPanel.hidden = true;
  el.confirmationCandidates.innerHTML = "";
  el.statusStrip.hidden = true;
  el.requestId.textContent = "";
  setRevision(null);
  el.cancel.hidden = true;
  el.cancel.disabled = false;
  el.cancel.textContent = "取消";
  const feedback = $("#feedback-row");
  if (feedback) feedback.remove();
  clearError();
}

const hooks = {
  status(current, status, text) {
    el.statusStrip.hidden = false;
    el.statusDot.className = `status-dot ${statusClass(status)}`;
    el.statusText.textContent = text;
    el.requestId.textContent = current.requestId ? `request ${current.requestId}` : "";
    setRevision(current.revision);
    el.cancel.hidden = !["accepted", "running", "waiting_user"].includes(status) || current.done;
    if (["accepted", "running"].includes(status) || (status === "waiting_user" && !current.done)) {
      el.answerPanel.hidden = true;
    }
    syncControls();
  },
  revision(value) {
    setRevision(value);
  },
  task(current, card) {
    const node = renderTaskCard(card);
    const existing = el.tasks.querySelector(`[data-task-id="${CSS.escape(card.task_id)}"]`);
    if (existing) existing.replaceWith(node);
    else el.tasks.appendChild(node);
  },
  answer(current, streaming) {
    if (streaming && !current.done) {
      el.answerPanel.hidden = false;
      el.answerText.classList.remove("is-rich");
      el.answerText.textContent = current.answer || "正在整理回答…";
      el.answerStatus.textContent = "生成中";
      el.answerStatus.className = "pill";
      return;
    }
    updateAnswerPanel(current.status);
  },
  confirmation(current, confirmations) {
    renderConfirmations(confirmations);
  },
  done(current, status) {
    el.statusStrip.hidden = false;
    el.statusDot.className = `status-dot ${statusClass(status)}`;
    el.statusText.textContent =
      status === "waiting_user" && (current.confirmations || []).length
        ? "等待确认查询对象"
        : OVERALL_LABELS[status] || status;
    el.requestId.textContent = current.requestId ? `request ${current.requestId}` : "";
    setRevision(current.revision);
    el.cancel.hidden = true;
    updateAnswerPanel(status);
    ensureFeedbackRow();
    syncControls();
  },
  error(current, message) {
    showError(message);
    syncControls();
  },
  busy(value) {
    submitting = value;
    syncControls();
  },
  confirming(current, value) {
    el.confirmationCandidates.querySelectorAll("button").forEach((item) => {
      item.disabled = value;
    });
  },
};

async function submitQuery() {
  if (submitting) return;
  const query = el.input.value.trim();
  if (!query) {
    showError("请输入要查询的问题。");
    el.input.focus();
    return;
  }
  if (turn && !turn.done) return;
  if (turn) turn.dispose();
  resetView();
  submitting = true;
  syncControls();
  turn = conversation.createTurn(query, hooks);
  await turn.start();
  submitting = false;
  syncControls();
}

async function cancelQuery() {
  if (!turn || !turn.requestId || turn.done) return;
  el.cancel.disabled = true;
  el.cancel.textContent = "取消中…";
  await turn.cancel();
  el.cancel.disabled = false;
  el.cancel.textContent = "取消";
}

async function loadHealth() {
  try {
    const health = await request("/api/v1/health");
    el.fixedTime.textContent = fmtDateTime(health.fixed_query_time, false);
    el.fixedTime.title = `固定查询锚点（${health.timezone}），演示数据不代表实时状态`;
    el.bundleVersion.textContent = health.config_bundle_version || "—";
    el.bundleVersion.title = `已发布配置包：${health.config_bundle_version || "未知"}`;
    if (health.source_mode && health.source_mode !== "mock") {
      el.mockBadge.textContent =
        health.source_mode === "platform" ? "platform 平台数据" : `${health.source_mode} 来源`;
      el.mockBadge.className = "badge badge-platform";
    }
    el.sourcePill.textContent = `来源分离 · ${health.llm_enabled ? "LLM 解析可用" : "规则解析"} · 未知不等于正常`;
  } catch (error) {
    el.fixedTime.textContent = "—";
    el.bundleVersion.textContent = "—";
    showError(`服务状态读取失败：${error.message}。请确认后端已启动。`);
  }
}

function bindEvents() {
  el.form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuery();
  });
  el.input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      el.form.requestSubmit();
    }
  });
  el.examples.addEventListener("click", (event) => {
    const button = event.target.closest("[data-query]");
    if (!button) return;
    el.input.value = button.getAttribute("data-query") || "";
    el.input.focus();
  });
  el.confirmationCandidates.addEventListener("click", (event) => {
    const button = event.target.closest(".candidate");
    if (!button || !turn || turn.confirming) return;
    turn.chooseCandidate(button.getAttribute("data-confirmation"), button.getAttribute("data-selected"));
  });
  el.cancel.addEventListener("click", cancelQuery);
}

function init() {
  bindEvents();
  loadHealth();
  conversation.ensureSession().catch((error) => {
    showError(`会话初始化失败：${error.message}。提交查询时会再次尝试。`);
  });
}

init();
