"use strict";

const state = {
  health: null,
  conversations: [],
  activeId: new URL(window.location.href).searchParams.get("case") || "S00018",
  current: null,
  risks: null,
  selectedRiskId: Number(new URL(window.location.href).searchParams.get("risk")) || null,
  riskFilter: ["all", "pending", "processing", "attention", "closed"].includes(new URL(window.location.href).searchParams.get("filter"))
    ? new URL(window.location.href).searchParams.get("filter")
    : "all",
  initialView: new URL(window.location.href).searchParams.get("view") === "risk" ? "risk" : "service",
  revisionSessionId: null,
  draftEdited: false,
  noteEdited: false,
  activeVersion: null,
  conversationLoading: false,
  pollBusy: false,
  draftedVersions: new Set(),
  refinementTimer: null,
  unreadTotal: 0,
  loadSequence: 0,
  inboxView: "all",
  snoozedIds: new Set(),
};

const showcaseCases = [
  { id: "S00018", label: "跨系统核对", icon: "🧾" },
  { id: "S00001", label: "图片凭证", icon: "📷" },
  { id: "S00082", label: "安全回访", icon: "🛡️" },
  { id: "S00019", label: "个性建议", icon: "💄" },
];
const currencyFormatter = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function inputDateTime(value) {
  return value ? value.replace(" ", "T").slice(0, 16) : "";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "服务暂时不可用");
  }
  return data;
}

function refreshIcons() {
  if (!window.lucide) return;
  window.lucide.createIcons();
  document.querySelectorAll("svg.lucide").forEach((icon) => {
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
  });
}

function showToast(text, icon = "circle-check") {
  const toast = document.querySelector("#toast");
  document.querySelector("#toastText").textContent = text;
  const oldIcon = toast.querySelector("svg, i");
  if (oldIcon) oldIcon.outerHTML = `<i data-lucide="${icon}"></i>`;
  refreshIcons();
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function setLoading(button, loading) {
  button.disabled = loading;
  button.classList.toggle("loading", loading);
  button.classList.toggle("busy", loading);
}

function updateUrl(panel = "assist") {
  if (document.querySelector(".app")?.classList.contains("risk-mode")) return;
  const url = new URL(window.location.href);
  url.searchParams.set("case", state.activeId);
  url.searchParams.delete("view");
  url.searchParams.delete("filter");
  url.searchParams.delete("risk");
  url.hash = panel;
  window.history.replaceState(null, "", url);
}

function updateRiskUrl() {
  const url = new URL(window.location.href);
  url.searchParams.set("view", "risk");
  url.searchParams.set("filter", state.riskFilter);
  if (state.selectedRiskId) url.searchParams.set("risk", String(state.selectedRiskId));
  else url.searchParams.delete("risk");
  url.hash = "";
  window.history.replaceState(null, "", url);
}

function avatarColor(id) {
  const colors = ["#7d8eb1", "#a77a78", "#658f82", "#8a81a8", "#aa825c", "#697f9d"];
  const hash = [...String(id)].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return colors[hash % colors.length];
}

function bundleVersion(bundle) {
  const messages = bundle?.messages || [];
  const last = messages[messages.length - 1];
  const serviceMode = bundle?.service_state?.service_mode || "ai";
  return last ? `${last.message_id}:${last.message_seq}:${serviceMode}` : `empty:${serviceMode}`;
}

function decorateShowcaseConversations(items) {
  return showcaseCases
    .map((showcase) => {
      const conversation = items.find((item) => item.session_id === showcase.id);
      return conversation ? { ...conversation, showcase_label: showcase.label, showcase_icon: showcase.icon } : null;
    })
    .filter(Boolean);
}

function updateUnreadIndicators() {
  state.unreadTotal = state.conversations.reduce((sum, item) => sum + Number(item.unread_count || 0), 0);
  const badge = document.querySelector("#messageBadge");
  badge.textContent = String(state.unreadTotal);
  badge.hidden = state.unreadTotal === 0;
  const statValues = document.querySelectorAll(".work-stat strong");
  if (statValues[0]) statValues[0].textContent = String(state.unreadTotal);
}

function renderSystemState() {
  const health = state.health;
  const topState = document.querySelector("#systemState");
  const badge = document.querySelector("#aiModeBadge");
  if (!health) {
    topState.textContent = "正在连接服务助手…";
    badge.textContent = "连接中";
    return;
  }
  if (health.ai.configured) {
    topState.textContent = "智能分析已开启";
    badge.textContent = "实时";
    badge.className = "live-badge";
  } else {
    topState.textContent = "演示模式已就绪";
    badge.textContent = "演示模式";
    badge.className = "live-badge offline";
  }
  document.querySelector("#riskBadge").textContent = String(health.open_risks || 0);
  const statValues = document.querySelectorAll(".work-stat strong");
  if (statValues[0]) statValues[0].textContent = String(state.unreadTotal);
  if (statValues[1]) statValues[1].textContent = String(health.open_risks || 0);
  if (statValues[2]) statValues[2].textContent = String(health.overdue_commitments || 0);
  document.querySelector("#modelStateStat").textContent = health.ai.configured ? "在线" : "保障";
  document.querySelector("#modelStateStat").title = health.ai.configured
    ? "会话分析服务在线"
    : "本地确定性规则模式";
  updateUnreadIndicators();
}

function renderConversationList(filter = "") {
  const target = document.querySelector("#conversationList");
  const normalized = filter.trim().toLowerCase();
  const resolvedPattern = /(问题已经解决|已经收到正确商品|确认没问题|没问题了)/;
  const items = state.conversations.filter((item) => {
    const matchesSearch = [item.session_id, item.buyer_nickname, item.scene_minor, item.preview]
      .join(" ")
      .toLowerCase()
      .includes(normalized);
    if (!matchesSearch) return false;
    if (state.inboxView === "later") return state.snoozedIds.has(item.session_id);
    if (state.inboxView === "completed") return Boolean(item.is_completed) || resolvedPattern.test(item.preview || "");
    return !state.snoozedIds.has(item.session_id) && !item.is_completed;
  });
  const viewLabels = { all: "重点会话", later: "稍后处理", completed: "已完成" };
  document.querySelector(".conversation-summary strong").textContent = `${viewLabels[state.inboxView] || "重点会话"} ${items.length}`;
  document.querySelector(".conversation-summary span").textContent = state.inboxView === "all" ? "4 类关键场景" : "演示会话";
  if (!items.length) {
    target.innerHTML = '<div class="empty-state" role="status">没有找到相关会话，请尝试搜索会话号或问题类型。</div>';
    return;
  }
  target.innerHTML = items
    .map((item) => {
      const active = item.session_id === state.activeId;
      return `
        <button class="conversation-item${active ? " active" : ""}" type="button"
          data-session-id="${escapeHtml(item.session_id)}" aria-pressed="${String(active)}"
          aria-label="${escapeHtml(item.buyer_nickname)}，${escapeHtml(item.scene_minor)}">
          <span class="customer-avatar" style="--avatar:${avatarColor(item.session_id)}">${escapeHtml((item.buyer_nickname || "客").slice(0, 1))}</span>
          <span class="conversation-copy">
            <span class="conversation-name">${escapeHtml(item.buyer_nickname)}<span><i class="case-emoji" aria-hidden="true">${escapeHtml(item.showcase_icon || "💬")}</i>${escapeHtml(item.showcase_label || item.scene_major)}</span></span>
            <span class="conversation-preview">${escapeHtml(item.preview || item.scene_minor)}</span>
          </span>
          <span class="conversation-meta">${escapeHtml(formatTime(item.last_message_at))}${item.unread_count ? `<span class="unread" aria-label="${item.unread_count} 条未读">${item.unread_count}</span>` : ""}</span>
        </button>`;
    })
    .join("");
}

function renderChat(bundle) {
  const conversation = bundle.conversation;
  const order = bundle.order;
  const avatar = document.querySelector("#chatAvatar");
  avatar.textContent = (conversation.buyer_nickname || "客").slice(0, 1);
  avatar.style.setProperty("--avatar", avatarColor(conversation.session_id));
  document.querySelector("#chatName").textContent = conversation.buyer_nickname;
  document.querySelector("#chatStatus").textContent = `● 在线 · ${conversation.session_id}`;
  const serviceState = bundle.service_state || {};
  const serviceButton = document.querySelector("#serviceModeButton");
  serviceButton.disabled = false;
  const isAi = serviceState.service_mode === "ai";
  serviceButton.className = `service-mode-button ${isAi ? "ai" : serviceState.handoff_reason ? "handoff" : "human"}`;
  serviceButton.innerHTML = `<i data-lucide="${isAi ? "bot" : "headset"}"></i><span>${isAi ? "AI 接待中" : "人工接待中"}</span>`;
  serviceButton.title = isAi
    ? "点击由人工客服接管"
    : serviceState.handoff_reason
      ? `转人工原因：${serviceState.handoff_reason}。点击交回 AI 接待`
      : "点击交回 AI 接待";

  const orderHtml = order
    ? `<div class="order-card">
        <div class="order-label"><span>关联订单 ${escapeHtml(order.order_id)}</span><strong>${escapeHtml(order.status)}</strong></div>
        <div class="product-row">
          <div class="product-thumb">${escapeHtml((order.sku || "SKU").slice(-3))}</div>
          <div class="product-copy"><strong>${escapeHtml(order.product_name)}</strong><span>${escapeHtml([order.carrier, order.tracking_no].filter(Boolean).join(" ") || "暂无物流信息")}</span></div>
          <div class="price">${order.paid_amount == null ? "--" : escapeHtml(currencyFormatter.format(order.paid_amount))}</div>
        </div>
      </div>`
    : "";
  const messagesHtml = bundle.messages
    .map((message) => {
      const isAgent = message.role === "agent";
      const body =
        message.content_type === "image"
          ? `<div class="image-attachment${message.image_url ? " has-preview" : ""}">${message.image_url
              ? `<a href="${escapeHtml(message.image_url)}" target="_blank" rel="noopener" aria-label="打开用户上传的原图"><img class="message-photo" src="${escapeHtml(message.image_url)}" alt="用户上传的服务图片" width="84" height="84" /></a>`
              : '<div class="image-placeholder"><i data-lucide="image"></i></div>'}<div class="image-copy"><strong>${escapeHtml(message.text || "用户已提供图片")}</strong><span>${message.image_url ? "原图已同步，可点击查看" : "官方数据未附原图，仅保留上传记录"}</span></div></div>`
          : `<div>${escapeHtml(message.text)}</div>`;
      const bubble = `<div class="bubble">${body}<div class="bubble-time">${escapeHtml(formatTime(message.sent_at))} · <button class="evidence-button" data-evidence-id="${escapeHtml(message.message_id)}" type="button">查看来源</button></div></div>`;
      const agentAvatar = message.sender === "暖心客服" ? "AI" : "林";
      return `<div class="bubble-row ${isAgent ? "agent" : "user"}">${isAgent ? bubble : `<div class="bubble-avatar">${escapeHtml((conversation.buyer_nickname || "客").slice(0, 1))}</div>${bubble}`}${isAgent ? `<div class="bubble-avatar">${agentAvatar}</div>` : ""}</div>`;
    })
    .join("");
  const feed = document.querySelector("#chatFeed");
  feed.innerHTML = `<div class="system-time">官方 MOCK 数据 · 已关联聊天、订单与工单</div>${orderHtml}${messagesHtml}`;
  feed.scrollTop = feed.scrollHeight;
  refreshIcons();
}

function sourceButton(sourceId, label = "查看原始证据", compact = false) {
  if (!sourceId) return "";
  const accessibleLabel = escapeHtml(label);
  return `<button class="source-link${compact ? " source-link-icon" : ""}" data-evidence-id="${escapeHtml(sourceId)}" type="button" title="${accessibleLabel}" aria-label="${accessibleLabel}"><i data-lucide="file-search"></i>${compact ? "" : accessibleLabel}</button>`;
}

function renderCopilot(payload) {
  const { bundle, analysis, draft } = payload;
  const emotionIcons = { 满意: "😊", 平稳: "🙂", 着急: "⏱️", 担心: "😟", 不满: "😕", 愤怒: "😠" };
  const emotion = analysis.emotion_state?.value || "待判断";
  const emotionTrend = analysis.emotion_state?.trend || "";
  const emotionEscalated = emotion === "愤怒" || (emotionTrend.includes("升级") && !emotionTrend.includes("无明显升级"));
  document.querySelector("#signalEmotion").textContent = `${emotion}${emotionEscalated ? " ↑" : ""}`;
  document.querySelector("#signalEmotion").classList.toggle("escalated", emotionEscalated);
  document.querySelector("#signalEmotionIcon").textContent = emotionIcons[emotion] || "💬";
  const needsClarification = Boolean(analysis.primary_intent?.requires_clarification);
  const intentLabel = needsClarification ? "需要确认" : analysis.primary_intent?.value || "待确认";
  document.querySelector("#signalStatus").textContent = intentLabel;
  const resolution = analysis.resolution_state || {};
  const resolutionScore = Math.max(0, Math.min(Number(resolution.score) || 0, 100));
  document.querySelector("#resolutionStage").textContent = resolution.stage || "待确认";
  document.querySelector("#resolutionScore").textContent = `${resolutionScore}%`;
  document.querySelector("#resolutionSummary").textContent = resolution.summary || "正在整理当前处理情况。";
  document.querySelector("#resolutionProgress").setAttribute("aria-valuenow", String(resolutionScore));
  const completedSegments = Math.round(resolutionScore / 20);
  document.querySelectorAll("#resolutionProgress span").forEach((segment, index) => {
    segment.className = index < completedSegments - 1 ? "complete" : index === completedSegments - 1 ? "current" : "";
  });
  const run = analysis.run || {};
  const assistantSubtitle = document.querySelector("#assistantSubtitle");
  assistantSubtitle.textContent = "已整理当前会话";
  const aiModeBadge = document.querySelector("#aiModeBadge");
  aiModeBadge.title = run.provider?.startsWith("qwen")
    ? "会话状态已更新"
    : state.health?.ai?.configured
      ? "快速分析已完成"
      : "基础保障中";

  if (resolution.stage === "已解决") {
    const serviceButton = document.querySelector("#serviceModeButton");
    serviceButton.className = "service-mode-button resolved";
    serviceButton.innerHTML = '<i data-lucide="circle-check"></i><span>服务已完成</span>';
    serviceButton.title = "客户已确认问题解决";
    serviceButton.disabled = true;
  }

  const summary = document.querySelector("#needSummary");
  summary.textContent = analysis.summary;
  document.querySelector(".signal-problem").classList.toggle("alert", analysis.risk_level === "high");

  const visualObservations = analysis.visual_observations || [];
  const visualSection = document.querySelector("#visualSection");
  visualSection.hidden = visualObservations.length === 0;
  if (visualObservations.length) {
    const visualLabels = {
      product_label: "商品信息",
      package_damage: "包裹情况",
      skin_condition: "可见肤况",
      other: "图片内容",
    };
    document.querySelector("#visualState").textContent = visualObservations.some((item) => item.requires_review)
      ? "建议人工确认"
      : "已完成";
    document.querySelector("#visualInsights").innerHTML = visualObservations
      .map((item) => {
        const confidence = Math.round((Number(item.confidence) || 0) * 100);
        const detail = [item.comparison, item.limitation].filter(Boolean).join(" · ");
        return `<div class="visual-result${item.requires_review ? " review" : ""}">
          <div class="visual-result-icon"><i data-lucide="${item.requires_review ? "scan-search" : "badge-check"}"></i></div>
          <div class="visual-result-copy"><strong>${escapeHtml(visualLabels[item.type] || visualLabels.other)}：${escapeHtml(item.finding)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}${sourceButton(item.message_id, "查看图片")}</div>
          <span class="visual-confidence">${confidence ? `${confidence}%` : "待确认"}</span>
        </div>`;
      })
      .join("");
  }

  const visibleActions = (analysis.next_actions || []).slice(0, 2);
  document.querySelector("#nextStepCount").textContent = `${visibleActions.length} 步`;
  document.querySelector("#nextSteps").innerHTML = visibleActions
    .map(
      (item, index) => `<div class="plain-row${item.priority === "high" ? " warning" : ""}"><div class="plain-row-icon">${index + 1}</div><div class="plain-row-copy"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.detail)}</span></div></div>`,
    )
    .join("");

  document.querySelector("#suggestedReply").value = draft.reply_draft;
  state.draftEdited = false;
  document.querySelector("#replyTags").innerHTML = (draft.tags || []).slice(0, 2)
    .map((tag, index) => `<span class="chip${index === 0 ? " blue" : ""}">${escapeHtml(tag)}</span>`)
    .join("");
  document.querySelector("#suggestionSource").textContent =
    draft.provider === "qwen" ? "已使用智能生成，发送前仍会独立检查" : "当前为可离线使用的建议回复";
  if (state.revisionSessionId !== state.activeId) {
    state.revisionSessionId = state.activeId;
    document.querySelector("#revisionInstruction").value = "";
    setRegeneratePopover(false);
  }

  const memory = analysis.memory || [];
  const profile = analysis.customer_profile || {};
  const profileTraits = profile.traits || [];
  document.querySelector("#profileCount").textContent = `${profileTraits.length} 项`;
  document.querySelector("#profileTraits").innerHTML = profileTraits
    .map((item) => `<div class="profile-trait"><div class="profile-trait-copy"><small>${escapeHtml(item.label)}</small><strong>${escapeHtml(item.value)}</strong></div>${sourceButton(item.evidence?.[0], `查看${item.label}依据`, true)}</div>`)
    .join("") || '<div class="api-empty">本轮暂无明确偏好</div>';
  document.querySelector("#journeyTimeline").innerHTML = memory
    .filter((item) => item.kind === "service_event" || item.kind === "commitment")
    .sort((left, right) => new Date(left.event_time || 0) - new Date(right.event_time || 0))
    .map((item) => {
      const status =
        item.display_status ||
        ({ confirmed: "已记录", disputed: "待核对" }[item.status] || item.status || "已记录");
      return `<button class="journey-event" type="button" data-evidence-id="${escapeHtml(item.source_id)}" aria-label="查看${escapeHtml(item.label)}详细记录"><div class="journey-marker" aria-hidden="true"></div><time>${escapeHtml(formatTime(item.event_time))}</time><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(status)}</span></button>`;
    })
    .join("") || '<div class="api-empty">暂无跨系统服务事件</div>';
  document.querySelector("#knownFacts").innerHTML = memory
    .filter((item) => item.kind === "service_fact")
    .map(
      (item) => `<div class="plain-row known-fact-row"><div class="plain-row-icon"><i data-lucide="check"></i></div><div class="plain-row-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.value)}</span></div>${sourceButton(item.source_id, "查看原始证据", true)}</div>`,
    )
    .join("") || '<div class="api-empty">暂无已确认信息</div>';

  const ticket = bundle.tickets[0];
  const ticketStatus = document.querySelector("#ticketStatus");
  ticketStatus.textContent = ticket?.status || "无工单";
  ticketStatus.className = `status-pill${analysis.risk_level === "high" ? " danger" : ""}`;
  document.querySelector("#ticketBox").innerHTML = ticket
    ? [
        ["工单号", ticket.ticket_id],
        ["类型", ticket.category || ticket.ticket_kind],
        ["物流 / 批次", ticket.tracking_no || ticket.batch_no || "--"],
        ["当前处理人", ticket.owner || "待分配"],
      ]
        .map((field) => `<div class="ticket-field"><small>${field[0]}</small><strong>${escapeHtml(field[1])}</strong></div>`)
        .join("")
    : '<div class="api-empty">本会话暂无关联工单</div>';

  document.querySelector("#progressTimeline").innerHTML = bundle.tickets
    .map(
      (item, index) => `<div class="trace-step"><div class="trace-index">${index + 1}</div><div class="trace-copy"><strong>${escapeHtml(item.status || "待处理")}</strong><span>${escapeHtml([item.category, item.reason].filter(Boolean).join(" / "))}</span>${sourceButton(item.ticket_id)}</div></div>`,
    )
    .join("") || '<div class="api-empty">当前仍在接待阶段</div>';

  const serviceNote = bundle.service_note;
  if (!state.noteEdited) {
    document.querySelector("#serviceNote").value = serviceNote?.note || "";
  }
  document.querySelector("#serviceNoteMeta").textContent = serviceNote
    ? `${serviceNote.actor} · ${formatTime(serviceNote.updated_at)}`
    : "本轮服务记录";

  refreshIcons();
}

async function refreshDynamicDraft(sessionId, version) {
  if (!state.health?.ai?.configured) return;
  const requestKey = `${sessionId}:${version}`;
  if (state.draftedVersions.has(requestKey)) return;
  state.draftedVersions.add(requestKey);
  try {
    const draft = await api(`/api/conversations/${encodeURIComponent(sessionId)}/draft`, {
      method: "POST",
      body: JSON.stringify({ tone: "自然" }),
    });
    if (state.activeId !== sessionId || state.activeVersion !== version) return;
    state.current.draft = draft;
    if (state.draftEdited) return;
    document.querySelector("#suggestedReply").value = draft.reply_draft;
    document.querySelector("#replyTags").innerHTML = (draft.tags || []).slice(0, 2)
      .map((tag, index) => `<span class="chip${index === 0 ? " blue" : ""}">${escapeHtml(tag)}</span>`)
      .join("");
    document.querySelector("#suggestionSource").textContent = draft.provider?.startsWith("qwen")
      ? `${draft.provider === "qwen-guarded" ? "草稿已通过安全改写" : "已结合本轮信息生成"}`
      : "已使用本地保障回复";
  } catch (error) {
    state.draftedVersions.delete(requestKey);
  }
}

function scheduleRefinement(sessionId, version, attempt = 0) {
  clearTimeout(state.refinementTimer);
  if (attempt >= 10) return;
  state.refinementTimer = setTimeout(async () => {
    if (state.activeId !== sessionId || state.activeVersion !== version) return;
    try {
      const payload = await api(`/api/conversations/${encodeURIComponent(sessionId)}`);
      if (state.activeId !== sessionId || state.activeVersion !== version) return;
      if (payload.analysis?.run?.pending) {
        scheduleRefinement(sessionId, version, attempt + 1);
        return;
      }
      state.current = payload;
      renderCopilot(payload);
      void refreshDynamicDraft(sessionId, version);
    } catch {
      scheduleRefinement(sessionId, version, attempt + 1);
    }
  }, attempt < 3 ? 1000 : 1600);
}

async function markConversationRead(sessionId) {
  try {
    const result = await api(`/api/conversations/${encodeURIComponent(sessionId)}/read`, { method: "POST" });
    const item = state.conversations.find((conversation) => conversation.session_id === sessionId);
    if (item) item.unread_count = 0;
    state.unreadTotal = Number(result.total_unread || 0);
    renderConversationList(document.querySelector("#searchInput").value);
    updateUnreadIndicators();
  } catch {
    // Reading a conversation must remain usable if the badge update fails.
  }
}

async function switchServiceMode() {
  if (!state.current) return;
  const button = document.querySelector("#serviceModeButton");
  const currentMode = state.current.bundle.service_state?.service_mode || "ai";
  const nextMode = currentMode === "ai" ? "human" : "ai";
  setLoading(button, true);
  try {
    const serviceState = await api(`/api/conversations/${encodeURIComponent(state.activeId)}/service-mode`, {
      method: "PATCH",
      body: JSON.stringify({ mode: nextMode, reason: nextMode === "human" ? "客服主动接管" : null }),
    });
    state.current.bundle.service_state = serviceState;
    const item = state.conversations.find((conversation) => conversation.session_id === state.activeId);
    if (item) {
      item.service_mode = serviceState.service_mode;
      item.handoff_reason = serviceState.handoff_reason;
    }
    renderChat(state.current.bundle);
    showToast(nextMode === "human" ? "已由人工客服接管" : "后续新消息将由 AI 优先接待");
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(button, false);
  }
}

async function loadConversation(sessionId, force = false) {
  const loadId = ++state.loadSequence;
  state.conversationLoading = true;
  if (state.activeId !== sessionId || force) state.noteEdited = false;
  state.activeId = sessionId;
  renderConversationList(document.querySelector("#searchInput").value);
  try {
    const payload = force
      ? await api(`/api/conversations/${encodeURIComponent(sessionId)}/analyze`, {
          method: "POST",
          body: JSON.stringify({ force: true }),
        })
      : await api(`/api/conversations/${encodeURIComponent(sessionId)}`);
    if (loadId !== state.loadSequence || state.activeId !== sessionId) return;
    state.current = payload;
    state.activeVersion = bundleVersion(state.current.bundle);
    if (force) state.draftedVersions.delete(`${sessionId}:${state.activeVersion}`);
    renderChat(state.current.bundle);
    renderCopilot(state.current);
    updateUrl();
    await markConversationRead(sessionId);
    if (state.current.analysis?.run?.pending) {
      scheduleRefinement(state.activeId, state.activeVersion);
    } else {
      void refreshDynamicDraft(state.activeId, state.activeVersion);
    }
  } catch (error) {
    if (loadId !== state.loadSequence) return;
    showToast(error.message, "circle-alert");
  } finally {
    if (loadId === state.loadSequence) state.conversationLoading = false;
  }
}

async function pollStaffConversation() {
  if (
    document.hidden ||
    state.pollBusy ||
    state.conversationLoading ||
    !state.current ||
    document.querySelector(".app").classList.contains("risk-mode")
  ) return;

  state.pollBusy = true;
  const polledSessionId = state.activeId;
  try {
    const [payload, summaries] = await Promise.all([
      api(`/api/conversations/${encodeURIComponent(polledSessionId)}/bundle`),
      api("/api/conversations?limit=200"),
    ]);
    if (state.activeId !== polledSessionId) return;
    state.conversations = decorateShowcaseConversations(summaries.items);
    renderConversationList(document.querySelector("#searchInput").value);
    updateUnreadIndicators();
    const nextVersion = bundleVersion(payload.bundle);
    if (nextVersion === state.activeVersion) return;

    state.activeVersion = nextVersion;
    state.current.bundle = payload.bundle;
    const latest = payload.bundle.messages[payload.bundle.messages.length - 1];
    const listItem = state.conversations.find((item) => item.session_id === state.activeId);
    if (listItem && latest) {
      listItem.preview = latest.text;
      listItem.last_message_at = latest.sent_at;
      renderConversationList(document.querySelector("#searchInput").value);
    }
    renderChat(payload.bundle);
    await loadConversation(state.activeId);
  } catch {
    // Keep the current conversation usable while the next poll retries.
  } finally {
    state.pollBusy = false;
  }
}

async function regenerateReply() {
  const button = document.querySelector("#regenerateReply");
  setLoading(button, true);
  const instruction = document.querySelector("#revisionInstruction").value.trim();
  const tone = /简洁|简短|精简/.test(instruction)
    ? "简洁"
    : /共情|关心|温和/.test(instruction)
      ? "更关心"
      : "自然";
  const sessionId = state.activeId;
  const version = state.activeVersion;
  try {
    const draft = await api(`/api/conversations/${encodeURIComponent(sessionId)}/draft`, {
      method: "POST",
      body: JSON.stringify({ tone, instruction }),
    });
    if (state.activeId !== sessionId || state.activeVersion !== version) return;
    state.current.draft = draft;
    document.querySelector("#suggestedReply").value = draft.reply_draft;
    state.draftEdited = false;
    document.querySelector("#replyTags").innerHTML = (draft.tags || []).slice(0, 2)
      .map((tag, index) => `<span class="chip${index === 0 ? " blue" : ""}">${escapeHtml(tag)}</span>`)
      .join("");
    document.querySelector("#suggestionSource").textContent = draft.provider?.startsWith("qwen")
      ? `已按修改建议生成 · ${draft.latency_ms || 0} ms`
      : instruction
        ? "已按当前可支持的方向调整离线回复"
        : "已重新生成离线回复";
    setRegeneratePopover(false);
    showToast(instruction ? "已按修改建议重新生成" : "已重新生成回复");
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(button, false);
  }
}

function setRegeneratePopover(open) {
  const control = document.querySelector("#regenerateControl");
  const trigger = document.querySelector("#regenerateTrigger");
  const popover = document.querySelector("#revisionPopover");
  control.classList.toggle("open", open);
  trigger.setAttribute("aria-expanded", String(open));
  popover.setAttribute("aria-hidden", String(!open));
}

function showQualityIssues(result) {
  const strip = document.querySelector("#qualityStrip");
  const issues = result.issues || [];
  document.querySelector("#qualityMessage").textContent = issues.length
    ? issues.map((item) => item.message).join(" ")
    : "这条回复需要人工复核后再发送。";
  strip.hidden = false;
  refreshIcons();
}

async function sendReply() {
  const input = document.querySelector("#replyInput");
  const button = document.querySelector("#sendReply");
  const text = input.value.trim();
  const sessionId = state.activeId;
  if (!text) {
    showToast("请先输入回复内容", "circle-alert");
    return;
  }
  setLoading(button, true);
  document.querySelector("#qualityStrip").hidden = true;
  try {
    const result = await api(`/api/conversations/${encodeURIComponent(sessionId)}/send`, {
      method: "POST",
      body: JSON.stringify({ text, actor: "林小稚" }),
    });
    if (result.status === "blocked") {
      showQualityIssues(result.quality);
      showToast("已拦截需要复核的回复", "shield-alert");
      return;
    }
    if (state.activeId !== sessionId) return;
    input.value = "";
    await loadConversation(sessionId);
    showToast("回复已通过检查并发送");
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(button, false);
  }
}

async function saveServiceNote() {
  const input = document.querySelector("#serviceNote");
  const button = document.querySelector("#saveServiceNote");
  const note = input.value.trim();
  if (!note) {
    showToast("请先填写本轮服务备注", "circle-alert");
    input.focus();
    return;
  }
  setLoading(button, true);
  try {
    const completedSessionId = state.activeId;
    const currentIndex = state.conversations.findIndex(
      (item) => item.session_id === completedSessionId,
    );
    const saved = await api(`/api/conversations/${encodeURIComponent(state.activeId)}/note`, {
      method: "PUT",
      body: JSON.stringify({ note, actor: "林小稚", complete: true }),
    });
    state.current.bundle.service_note = saved;
    state.noteEdited = false;
    document.querySelector("#serviceNoteMeta").textContent = `${saved.actor} · ${formatTime(saved.updated_at)}`;
    const completedItem = state.conversations.find(
      (item) => item.session_id === completedSessionId,
    );
    if (completedItem) completedItem.is_completed = 1;
    const orderedCandidates = [
      ...state.conversations.slice(currentIndex + 1),
      ...state.conversations.slice(0, Math.max(currentIndex, 0)),
    ];
    const next = orderedCandidates.find(
      (item) => !item.is_completed && !state.snoozedIds.has(item.session_id),
    );
    document.querySelector("#searchInput").value = "";
    state.inboxView = "all";
    document.querySelectorAll("[data-inbox-view]").forEach((item) => {
      const active = item.dataset.inboxView === "all";
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    renderConversationList();
    if (next) {
      document.querySelector('.copilot-tab[data-panel="assist"]').click();
      await loadConversation(next.session_id);
      showToast("本轮接待已结束，已切换到下一位客户");
    } else {
      showToast("本轮接待已结束，当前没有下一位待处理客户");
    }
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(button, false);
  }
}

async function openEvidence(evidenceId) {
  const dialog = document.querySelector("#evidenceDialog");
  document.querySelector("#evidenceTitle").textContent = "正在读取";
  document.querySelector("#evidenceContent").innerHTML = '<div class="loading-block">正在查找原始记录…</div>';
  dialog.showModal();
  try {
    const evidence = await api(`/api/evidence/${encodeURIComponent(evidenceId)}`);
    const context = evidence.conversation_context || [];
    const conversationHtml = context.length
      ? `<section class="evidence-conversation" aria-labelledby="evidenceConversationTitle">
          <h3 id="evidenceConversationTitle">相关对话</h3>
          <div class="evidence-chat">${context
            .map((item) => {
              const isAgent = item.role === "agent";
              const avatar = isAgent ? (item.sender === "暖心客服" ? "AI" : "林") : "客";
              const content = item.is_key
                ? `<strong class="evidence-bubble-text">${escapeHtml(item.text)}</strong>`
                : `<div class="evidence-bubble-text">${escapeHtml(item.text)}</div>`;
              const bubble = `<div class="bubble">${item.is_key ? '<span class="evidence-key-label">关键对话</span>' : ""}${content}<div class="bubble-time">${escapeHtml(formatTime(item.sent_at))}</div></div>`;
              return `<div class="bubble-row evidence-chat-row ${isAgent ? "agent" : "user"}${item.is_key ? " key" : ""}">${isAgent ? bubble : `<div class="bubble-avatar">${avatar}</div>${bubble}`}${isAgent ? `<div class="bubble-avatar">${avatar}</div>` : ""}</div>`;
            })
            .join("")}</div>
        </section>`
      : "";
    const summaryHtml =
      evidence.source_type === "聊天" && context.length
        ? ""
        : `<div class="evidence-quote">${escapeHtml(evidence.content)}</div>`;
    document.querySelector("#evidenceTitle").textContent = evidence.title;
    document.querySelector("#evidenceContent").innerHTML = `
      ${evidence.media_url ? `<a class="evidence-media" href="${escapeHtml(evidence.media_url)}" target="_blank" rel="noopener"><img src="${escapeHtml(evidence.media_url)}" alt="原始证据图片" width="320" height="220" /><span>打开原图</span></a>` : ""}
      ${summaryHtml}
      ${conversationHtml}
      <div class="evidence-meta">
        <div><small>证据 ID</small><strong>${escapeHtml(evidence.id)}</strong></div>
        <div><small>来源</small><strong>${escapeHtml(evidence.source_sheet)}</strong></div>
        <div><small>原始行</small><strong>${escapeHtml(evidence.source_row || "本地操作")}</strong></div>
        <div><small>数据状态</small><strong>可追溯</strong></div>
      </div>`;
  } catch (error) {
    document.querySelector("#evidenceTitle").textContent = "无法读取证据";
    document.querySelector("#evidenceContent").innerHTML = `<div class="api-empty">${escapeHtml(error.message)}</div>`;
  }
}

function setView(view) {
  const riskMode = view === "risk";
  document.querySelector(".app").classList.toggle("risk-mode", riskMode);
  document.querySelector("#riskView").hidden = !riskMode;
  document.querySelector("#serviceNav").classList.toggle("active", !riskMode);
  document.querySelector("#riskNav").classList.toggle("active", riskMode);
  document.querySelector("#serviceNav").setAttribute("aria-current", riskMode ? "false" : "page");
  document.querySelector("#riskNav").setAttribute("aria-current", riskMode ? "page" : "false");
  if (riskMode) {
    updateRiskUrl();
    loadRisks();
  } else {
    const activePanel = document.querySelector(".copilot-tab.active")?.dataset.panel || "assist";
    updateUrl(activePanel);
  }
  refreshIcons();
}

function riskItems() {
  const items = state.risks?.risks || [];
  if (state.riskFilter === "pending") return items.filter((item) => item.status === "待处理");
  if (state.riskFilter === "processing") return items.filter((item) => ["处理中", "待回访"].includes(item.status));
  if (state.riskFilter === "attention") return items.filter((item) => item.needs_intervention);
  if (state.riskFilter === "closed") return items.filter((item) => item.status === "已关闭");
  return items.filter((item) => item.status !== "已关闭");
}

function riskWaitLabel(minutes) {
  const value = Number(minutes) || 0;
  if (value < 1) return "刚刚更新";
  if (value < 60) return `未更新 ${value} 分钟`;
  if (value < 1440) return `未更新 ${Math.floor(value / 60)} 小时`;
  return `未更新 ${Math.floor(value / 1440)} 天`;
}

function setRiskFilter(filter) {
  state.riskFilter = filter;
  document.querySelectorAll("[data-risk-filter]").forEach((button) => {
    const active = button.dataset.riskFilter === filter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const items = riskItems();
  if (!items.some((item) => item.id === state.selectedRiskId)) {
    state.selectedRiskId = items[0]?.id || null;
  }
  renderRiskKpis();
  renderRiskTable();
  renderRiskDetail();
  updateRiskUrl();
}

function renderRiskKpis() {
  const summary = state.risks?.summary || { pending: 0, processing: 0, attention: 0, closed_today: 0 };
  document.querySelector("#riskKpis").innerHTML = [
    ["待响应", summary.pending, "pending", "inbox", ""],
    ["处理中", summary.processing, "processing", "loader-circle", ""],
    ["需要管理者介入", summary.attention, "attention", "triangle-alert", "warning"],
    ["今日已关闭", summary.closed_today, "closed", "circle-check", "success"],
  ]
    .map(
      ([label, value, filter, icon, tone]) =>
        `<button class="risk-kpi ${tone}${state.riskFilter === filter ? " active" : ""}" type="button" data-kpi-filter="${filter}"><i data-lucide="${icon}"></i><span><small>${label}</small><strong>${value}</strong></span></button>`,
    )
    .join("");
  refreshIcons();
}

function renderRiskTable() {
  const items = riskItems();
  const filterLabels = { all: "进行中", pending: "待响应", processing: "处理中", attention: "需介入", closed: "已关闭" };
  document.querySelector("#riskListMeta").textContent = `${filterLabels[state.riskFilter]} ${items.length} 条 · 异常优先`;
  const table = document.querySelector("#riskTable");
  if (!items.length) {
    table.innerHTML = '<div class="api-empty">当前队列没有事件。</div>';
    return;
  }
  table.innerHTML = `
    <div class="risk-table-head"><span>类型</span><span>客户与事件</span><span>处理队列</span><span>状态与负责人</span><span>最近更新</span></div>
    ${items
      .map(
        (item) => `<button class="risk-row${item.id === state.selectedRiskId ? " selected" : ""}${item.needs_intervention ? " needs-intervention" : ""}" type="button" data-risk-id="${item.id}">
          <span class="risk-type-mark ${item.severity}">${escapeHtml(item.category)}</span>
          <span><strong>${escapeHtml(item.buyer_nickname)} · ${escapeHtml(item.title)}</strong><small>${escapeHtml(item.scene_minor)} · ${escapeHtml(item.session_id)}</small><small class="risk-compact-meta">${escapeHtml(item.queue)} · ${escapeHtml(item.display_status)}</small></span>
          <span class="risk-queue">${escapeHtml(item.queue)}</span>
          <span><strong class="risk-status">${escapeHtml(item.display_status)}</strong><small>${escapeHtml(item.owner || "系统分配中")}</small></span>
          <span class="risk-wait${item.needs_intervention ? " alert" : ""}">${escapeHtml(item.status === "已关闭" ? formatTime(item.updated_at) : riskWaitLabel(item.waiting_minutes))}</span>
        </button>`,
      )
      .join("")}`;
}

function renderRiskDetail() {
  const item = (state.risks?.risks || []).find((risk) => risk.id === state.selectedRiskId);
  const target = document.querySelector("#riskDetail");
  if (!item) {
    target.innerHTML = '<div class="empty-detail"><i data-lucide="mouse-pointer-click"></i><strong>选择一条风险事件</strong><span>查看事实、证据和协作进展</span></div>';
    refreshIcons();
    return;
  }
  const commitments = (state.risks?.commitments || []).filter((commitment) => commitment.session_id === item.session_id);
  const factList = (facts, emptyText) =>
    facts.length
      ? `<ul class="risk-fact-list">${facts.map((fact) => `<li><strong>${escapeHtml(fact.label)}</strong><span>${escapeHtml(fact.value)}</span></li>`).join("")}</ul>`
      : `<p class="risk-empty-copy">${emptyText}</p>`;
  let actionPanel = `<div class="risk-action-panel"><p>事件已由系统分配给 ${escapeHtml(item.owner || "客服")}，管理者可监控响应，并在异常时催办或重新分配。</p><label for="riskProgressNote">管理备注（可选）</label><textarea id="riskProgressNote" rows="2" maxlength="1000" placeholder="记录催办要求或重新分配说明"></textarea><div class="risk-action-row"><button class="secondary-button" type="button" data-risk-action="reassign"><i data-lucide="refresh-cw"></i>重新自动分配</button><button class="primary-button" type="button" data-risk-action="remind"><i data-lucide="bell-ring"></i>催办客服</button></div></div>`;
  if (item.status === "待回访") {
    actionPanel = `<div class="risk-action-panel"><p>客服已提交处理结果，管理者复核事实、承诺履行情况和客户反馈后决定是否关闭。</p><label for="riskProgressNote">退回说明</label><textarea id="riskProgressNote" rows="2" maxlength="1000" placeholder="退回时填写需要客服补充或修正的内容"></textarea><label for="riskResolution">复核结果</label><textarea id="riskResolution" rows="2" maxlength="1000" placeholder="确认关闭时填写最终结论、已执行动作和客户反馈"></textarea><div class="risk-action-row"><button class="secondary-button" type="button" data-risk-action="return"><i data-lucide="undo-2"></i>退回补充</button><button class="primary-button" type="button" data-risk-action="approve"><i data-lucide="circle-check"></i>复核通过并关闭</button></div></div>`;
  } else if (item.status === "已关闭") {
    actionPanel = '<div class="risk-closed-state"><i data-lucide="circle-check"></i><span>该事件已由管理者复核关闭，处理结果已保留在协作记录中。</span></div>';
  }
  target.innerHTML = `
    <div class="risk-detail-head"><div class="risk-detail-labels"><span class="risk-type-mark ${item.severity}">${escapeHtml(item.category)}</span><span class="risk-state-pill">${escapeHtml(item.display_status)}</span>${item.needs_intervention ? '<span class="risk-attention-pill">需介入</span>' : ""}</div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.buyer_nickname)} · ${escapeHtml(item.queue)} · ${escapeHtml(item.owner || "系统分配中")}</p></div>
    <div class="risk-detail-body">
      <section><h4>事件摘要</h4><p>${escapeHtml(item.detail)}</p>${item.intervention_reason ? `<div class="risk-intervention-note"><i data-lucide="triangle-alert"></i>${escapeHtml(item.intervention_reason)}</div>` : ""}</section>
      <section><h4>已核实事实</h4>${factList(item.verified_facts || [], "暂未形成可核实事实")}</section>
      <section><h4>冲突事实</h4>${factList(item.conflicting_facts || [], "当前未发现系统记录冲突")}</section>
      <section><h4>待确认问题</h4>${factList((item.pending_questions || []).map((value) => ({ label: "需要专业确认", value })), "当前没有待确认问题")}</section>
      <section><h4>原始证据</h4><div class="risk-evidence-list">${(item.evidence || [])
        .map((evidence) => `<button type="button" data-evidence-id="${escapeHtml(evidence.id)}"><strong>${escapeHtml(evidence.label || evidence.source_type)}</strong><span>${escapeHtml(evidence.value)}</span></button>`)
        .join("") || '<p class="risk-empty-copy">暂无可打开的原始证据</p>'}</div></section>
      <section><h4>已作承诺</h4>${factList(commitments.map((commitment) => ({ label: commitment.status, value: commitment.content })), "当前没有对客户作出待履行承诺")}</section>
      <section><h4>协作记录</h4><div class="risk-activity">${(item.activities || []).map((activity) => `<div class="risk-activity-item"><span class="risk-activity-dot"></span><div><strong>${escapeHtml(activity.actor)} · ${escapeHtml(activity.action)}</strong><time>${escapeHtml(formatTime(activity.created_at))}</time>${activity.note ? `<p>${escapeHtml(activity.note)}</p>` : ""}</div></div>`).join("")}</div></section>
      <button class="risk-source-button" id="openRiskConversation" type="button" data-session-id="${escapeHtml(item.session_id)}"><i data-lucide="messages-square"></i>查看对话</button>
    </div>
    <div class="risk-detail-actions">${actionPanel}</div>`;
  refreshIcons();
}

async function loadRisks() {
  document.querySelector("#riskTable").innerHTML = '<div class="loading-block">正在同步风险事件…</div>';
  try {
    state.risks = await api("/api/risks?status=all");
    if (!state.selectedRiskId && state.risks.risks.length) state.selectedRiskId = state.risks.risks[0].id;
    if (!riskItems().some((item) => item.id === state.selectedRiskId)) {
      state.selectedRiskId = riskItems()[0]?.id || null;
    }
    renderRiskKpis();
    renderRiskTable();
    renderRiskDetail();
    document.querySelector("#riskBadge").textContent = String(state.risks.summary.open);
  } catch (error) {
    document.querySelector("#riskTable").innerHTML = `<div class="api-empty">${escapeHtml(error.message)}</div>`;
  }
}

async function runRiskAction(action, button) {
  const payload = {
    action,
    note: document.querySelector("#riskProgressNote")?.value.trim() || null,
    resolution: document.querySelector("#riskResolution")?.value.trim() || null,
  };
  if (action === "return" && !payload.note) {
    showToast("退回前请填写需要补充的内容", "circle-alert");
    document.querySelector("#riskProgressNote")?.focus();
    return;
  }
  if (action === "approve" && !payload.resolution) {
    showToast("复核关闭前必须填写处理结果", "circle-alert");
    document.querySelector("#riskResolution")?.focus();
    return;
  }
  setLoading(button, true);
  try {
    await api(`/api/risks/${state.selectedRiskId}`, { method: "PATCH", body: JSON.stringify(payload) });
    const labels = { remind: "已催办当前客服", reassign: "已重新自动分配", return: "已退回客服补充", approve: "事件已复核关闭" };
    showToast(labels[action] || "协作记录已更新");
    await loadRisks();
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(button, false);
  }
}

async function openRiskConversation(sessionId) {
  const dialog = document.querySelector("#riskConversationDialog");
  document.querySelector("#riskConversationTitle").textContent = "正在读取";
  document.querySelector("#riskConversationMeta").textContent = sessionId;
  document.querySelector("#riskConversationContent").innerHTML = '<div class="loading-block">正在加载客户对话…</div>';
  dialog.showModal();
  try {
    const payload = await api(`/api/conversations/${encodeURIComponent(sessionId)}/bundle`);
    const bundle = payload.bundle;
    const conversation = bundle.conversation;
    const messages = bundle.messages || [];
    document.querySelector("#riskConversationTitle").textContent = `${conversation.buyer_nickname}的对话`;
    document.querySelector("#riskConversationMeta").textContent = `${conversation.scene_major} · ${conversation.scene_minor} · ${conversation.session_id}`;
    document.querySelector("#riskConversationContent").innerHTML = `
      <div class="risk-conversation-feed evidence-chat">${messages
        .map((message) => {
          const isAgent = message.role === "agent";
          const avatar = isAgent ? (message.sender === "暖心客服" ? "AI" : "服") : (conversation.buyer_nickname || "客").slice(0, 1);
          const content =
            message.content_type === "image"
              ? `<div class="image-attachment${message.image_url ? " has-preview" : ""}">${message.image_url
                  ? `<a href="${escapeHtml(message.image_url)}" target="_blank" rel="noopener" aria-label="打开用户上传的原图"><img class="message-photo" src="${escapeHtml(message.image_url)}" alt="用户上传的服务图片" width="84" height="84" /></a>`
                  : '<div class="image-placeholder"><i data-lucide="image"></i></div>'}<div class="image-copy"><strong>${escapeHtml(message.text || "用户已提供图片")}</strong><span>${message.image_url ? "点击查看原图" : "原始数据未附图片文件"}</span></div></div>`
              : `<div class="evidence-bubble-text">${escapeHtml(message.text)}</div>`;
          const bubble = `<div class="bubble">${content}<div class="bubble-time">${escapeHtml(formatTime(message.sent_at))}</div></div>`;
          return `<div class="bubble-row evidence-chat-row ${isAgent ? "agent" : "user"}">${isAgent ? bubble : `<div class="bubble-avatar">${escapeHtml(avatar)}</div>${bubble}`}${isAgent ? `<div class="bubble-avatar">${escapeHtml(avatar)}</div>` : ""}</div>`;
        })
        .join("")}</div>`;
    document.querySelector("#riskConversationContent").scrollTop = 0;
    refreshIcons();
  } catch (error) {
    document.querySelector("#riskConversationTitle").textContent = "无法读取对话";
    document.querySelector("#riskConversationContent").innerHTML = `<div class="api-empty">${escapeHtml(error.message)}</div>`;
  }
}

function setupTabs() {
  document.querySelectorAll(".copilot-tab").forEach((tab) =>
    tab.addEventListener("click", () => {
      document.querySelectorAll(".copilot-tab").forEach((item) => {
        item.classList.remove("active");
        item.setAttribute("aria-selected", "false");
        item.tabIndex = -1;
      });
      document.querySelectorAll(".copilot-panel").forEach((item) => {
        item.classList.remove("active");
        item.hidden = true;
      });
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      tab.tabIndex = 0;
      const panel = document.querySelector(`#panel-${tab.dataset.panel}`);
      panel.hidden = false;
      panel.classList.add("active");
      updateUrl(tab.dataset.panel);
    }),
  );
  document.querySelector(".copilot-tabs").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll(".copilot-tab")];
    const current = tabs.indexOf(document.activeElement);
    if (current < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
    tabs[next].click();
  });
}

function setupEvents() {
  setupTabs();
  const openPanel = (panel) => document.querySelector(`.copilot-tab[data-panel="${panel}"]`)?.click();
  document.addEventListener("click", (event) => {
    const conversation = event.target.closest("[data-session-id]");
    if (conversation && !conversation.id?.includes("openRiskConversation")) {
      loadConversation(conversation.dataset.sessionId);
      return;
    }
    const evidence = event.target.closest("[data-evidence-id]");
    if (evidence) {
      openEvidence(evidence.dataset.evidenceId);
      return;
    }
    const risk = event.target.closest("[data-risk-id]");
    if (risk) {
      state.selectedRiskId = Number(risk.dataset.riskId);
      renderRiskTable();
      renderRiskDetail();
      updateRiskUrl();
      if (window.matchMedia("(max-width: 899px)").matches) {
        document.querySelector("#riskDetail").scrollIntoView({ block: "start" });
      }
    }
  });
  document.querySelector("#searchInput").addEventListener("input", (event) => renderConversationList(event.target.value));
  document.querySelector("#insertReply").addEventListener("click", () => {
    const input = document.querySelector("#replyInput");
    input.value = document.querySelector("#suggestedReply").value.trim();
    input.focus();
    document.querySelector("#qualityStrip").hidden = true;
    showToast("建议已放入输入框，可继续修改");
  });
  document.querySelector("#suggestedReply").addEventListener("input", () => {
    state.draftEdited = true;
  });
  document.querySelector("#regenerateReply").addEventListener("click", regenerateReply);
  const regenerateControl = document.querySelector("#regenerateControl");
  document.querySelector("#regenerateTrigger").addEventListener("click", () => {
    const open = !regenerateControl.classList.contains("open");
    setRegeneratePopover(open);
    if (open) document.querySelector("#revisionInstruction").focus();
  });
  regenerateControl.addEventListener("mouseenter", () => setRegeneratePopover(true));
  regenerateControl.addEventListener("mouseleave", () => {
    if (!regenerateControl.contains(document.activeElement)) setRegeneratePopover(false);
  });
  regenerateControl.addEventListener("focusout", () => {
    requestAnimationFrame(() => {
      if (!regenerateControl.contains(document.activeElement)) setRegeneratePopover(false);
    });
  });
  document.querySelector("#revisionInstruction").addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setRegeneratePopover(false);
      document.querySelector("#regenerateTrigger").focus();
    } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      regenerateReply();
    }
  });
  document.querySelector("#sendReply").addEventListener("click", sendReply);
  document.querySelector("#serviceNote").addEventListener("input", () => {
    state.noteEdited = true;
    document.querySelector("#serviceNoteMeta").textContent = "尚未保存";
  });
  document.querySelector("#saveServiceNote").addEventListener("click", saveServiceNote);
  document.querySelector("#serviceModeButton").addEventListener("click", switchServiceMode);
  document.querySelector("#messageNav").addEventListener("click", () => {
    const firstUnread = state.conversations.find((item) => Number(item.unread_count || 0) > 0);
    if (firstUnread) loadConversation(firstUnread.session_id);
    else showToast("当前没有未读消息");
  });
  document.querySelector("#replyInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendReply();
    }
  });
  document.querySelector("#reanalyze").addEventListener("click", async (event) => {
    setLoading(event.currentTarget, true);
    await loadConversation(state.activeId, true);
    setLoading(event.currentTarget, false);
    showToast("已重新读取当前会话");
  });
  document.querySelector("#dismissQuality").addEventListener("click", () => {
    document.querySelector("#qualityStrip").hidden = true;
  });
  document.querySelector("#serviceNav").addEventListener("click", () => setView("service"));
  document.querySelector("#customerNav").addEventListener("click", () => {
    setView("service");
    openPanel("journey");
  });
  document.querySelector("#ticketNav").addEventListener("click", () => {
    setView("service");
    openPanel("progress");
  });
  document.querySelector("#customerProfileButton").addEventListener("click", () => openPanel("journey"));
  document.querySelector("#orderHistoryButton").addEventListener("click", () => openPanel("progress"));
  document.querySelector("#settingsTool").addEventListener("click", () =>
    window.open("/evaluation", "_blank", "noopener"),
  );
  document.querySelector("#globalSearch").addEventListener("click", () => document.querySelector("#searchInput").focus());
  document.querySelector("#notificationTool").addEventListener("click", () => document.querySelector("#messageNav").click());
  document.querySelector("#knowledgeButton").addEventListener("click", () => openPanel("journey"));
  document.querySelector("#quickReplyButton").addEventListener("click", () => document.querySelector("#insertReply").click());
  document.querySelector("#emojiButton").addEventListener("click", () => {
    const input = document.querySelector("#replyInput");
    const start = input.selectionStart ?? input.value.length;
    input.setRangeText("🙂", start, input.selectionEnd ?? start, "end");
    input.focus();
  });
  document.querySelectorAll("[data-inbox-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.inboxView;
      if (view === "members") {
        showToast("当前接待客服：林小稚");
        return;
      }
      state.inboxView = view;
      document.querySelectorAll("[data-inbox-view]").forEach((item) => {
        const active = item.dataset.inboxView === view;
        item.classList.toggle("active", active);
        item.setAttribute("aria-pressed", String(active));
      });
      renderConversationList(document.querySelector("#searchInput").value);
    });
  });
  document.querySelector("#riskNav").addEventListener("click", () => setView("risk"));
  document.querySelector("#refreshRisks").addEventListener("click", loadRisks);
  document.querySelectorAll("[data-risk-filter]").forEach((button) =>
    button.addEventListener("click", () => setRiskFilter(button.dataset.riskFilter)),
  );
  document.querySelector("#riskKpis").addEventListener("click", (event) => {
    const kpi = event.target.closest("[data-kpi-filter]");
    if (kpi) setRiskFilter(kpi.dataset.kpiFilter);
  });
  document.querySelector("#riskDetail").addEventListener("click", (event) => {
    const actionButton = event.target.closest("[data-risk-action]");
    if (actionButton) {
      runRiskAction(actionButton.dataset.riskAction, actionButton);
      return;
    }
    const button = event.target.closest("#openRiskConversation");
    if (!button) return;
    openRiskConversation(button.dataset.sessionId);
  });
  document.querySelector("#closeConversation").addEventListener("click", () => {
    state.snoozedIds.add(state.activeId);
    document.querySelector('[data-inbox-view="later"]').click();
    showToast("会话已移入稍后处理");
  });
  document.querySelector("#closeEvidence").addEventListener("click", () => document.querySelector("#evidenceDialog").close());
  document.querySelector("#evidenceDialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
  document.querySelector("#closeRiskConversation").addEventListener("click", () => document.querySelector("#riskConversationDialog").close());
  document.querySelector("#riskConversationDialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
}

async function bootstrap() {
  setupEvents();
  refreshIcons();
  try {
    const [health, conversations] = await Promise.all([api("/api/health"), api("/api/conversations?limit=200")]);
    state.health = health;
    state.conversations = decorateShowcaseConversations(conversations.items);
    state.unreadTotal = state.conversations.reduce((sum, item) => sum + Number(item.unread_count || 0), 0);
    if (!state.conversations.some((item) => item.session_id === state.activeId)) {
      state.activeId = state.conversations[0]?.session_id || "S00018";
    }
    renderSystemState();
    renderConversationList();
    await loadConversation(state.activeId);
    const initialPanel = window.location.hash.replace("#", "");
    const initialTab = document.querySelector(`.copilot-tab[data-panel="${initialPanel}"]`);
    if (initialTab && initialPanel !== "assist") initialTab.click();
    document.querySelectorAll("[data-risk-filter]").forEach((button) => {
      const active = button.dataset.riskFilter === state.riskFilter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (state.initialView === "risk") setView("risk");
  } catch (error) {
    document.querySelector("#systemState").textContent = "后端服务未连接";
    document.querySelector("#aiModeBadge").textContent = "连接失败";
    document.querySelector("#aiModeBadge").className = "live-badge error";
    showToast(`${error.message}，请确认项目服务已启动`, "circle-alert");
  }
}

bootstrap();
setInterval(pollStaffConversation, 1200);
