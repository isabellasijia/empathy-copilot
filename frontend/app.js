"use strict";

const state = {
  health: null,
  conversations: [],
  activeId: new URL(window.location.href).searchParams.get("case") || "S00018",
  current: null,
  risks: null,
  selectedRiskId: Number(new URL(window.location.href).searchParams.get("risk")) || null,
  riskFilter: ["all", "high", "unassigned"].includes(new URL(window.location.href).searchParams.get("filter"))
    ? new URL(window.location.href).searchParams.get("filter")
    : "all",
  initialView: new URL(window.location.href).searchParams.get("view") === "risk" ? "risk" : "service",
  draftToneIndex: 0,
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

const tones = ["自然", "简洁", "更关心"];
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
    if (state.inboxView === "completed") return resolvedPattern.test(item.preview || "");
    return !state.snoozedIds.has(item.session_id);
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

function sourceButton(sourceId, label = "查看原始证据") {
  if (!sourceId) return "";
  return `<button class="source-link" data-evidence-id="${escapeHtml(sourceId)}" type="button"><i data-lucide="file-search"></i>${escapeHtml(label)}</button>`;
}

function renderCopilot(payload) {
  const { bundle, analysis, draft } = payload;
  const riskLabels = { high: "重点注意", medium: "需要留意", none: "正常" };
  const emotionIcons = { 满意: "😊", 平稳: "🙂", 着急: "⏱️", 担心: "😟", 不满: "😕", 愤怒: "😠" };
  const emotion = analysis.emotion_state?.value || "待判断";
  const emotionTrend = analysis.emotion_state?.trend || "";
  const emotionEscalated = emotion === "愤怒" || (emotionTrend.includes("升级") && !emotionTrend.includes("无明显升级"));
  document.querySelector("#signalEmotion").textContent = `${emotion}${emotionEscalated ? " ↑" : ""}`;
  document.querySelector("#signalEmotion").classList.toggle("escalated", emotionEscalated);
  document.querySelector("#signalEmotionIcon").textContent = emotionIcons[emotion] || "💬";
  document.querySelector("#signalRisk").textContent = riskLabels[analysis.risk_level] || "需关注";
  const needsClarification = Boolean(analysis.primary_intent?.requires_clarification);
  const intentLabel = needsClarification ? "需要确认" : analysis.primary_intent?.value || "待确认";
  document.querySelector("#signalStatus").textContent = intentLabel;
  const resolution = analysis.resolution_state || {};
  const resolutionScore = Math.max(0, Math.min(Number(resolution.score) || 0, 100));
  document.querySelector("#resolutionStage").textContent = resolution.stage || "待确认";
  document.querySelector("#resolutionScore").textContent = `${resolutionScore}%`;
  document.querySelector("#resolutionFill").style.transform = `scaleX(${resolutionScore / 100})`;
  document.querySelector("#resolutionSummary").textContent = resolution.summary || "正在整理当前处理情况。";
  document.querySelector("#resolutionProgress").setAttribute("aria-valuenow", String(resolutionScore));
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
  summary.className = `summary-callout${analysis.risk_level === "high" ? " alert" : ""}`;
  summary.innerHTML = `<i data-lucide="${analysis.risk_level === "high" ? "triangle-alert" : "message-circle-heart"}"></i><span>${escapeHtml(analysis.summary)}</span>`;

  const secondary = (analysis.secondary_intents || []).map((item) => item.value).join("、") || "暂无";
  document.querySelector("#intentDetails").innerHTML = `
    <div><small>当前问题</small><strong>${escapeHtml(intentLabel)}</strong></div>
    <div><small>同时关注</small><strong>${escapeHtml(secondary)}</strong></div>`;

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

  document.querySelector("#suggestedReply").textContent = draft.reply_draft;
  document.querySelector("#replyTags").innerHTML = (draft.tags || []).slice(0, 2)
    .map((tag, index) => `<span class="chip${index === 0 ? " blue" : ""}">${escapeHtml(tag)}</span>`)
    .join("");
  document.querySelector("#suggestionSource").textContent =
    draft.provider === "qwen" ? "已使用智能生成，发送前仍会独立检查" : "当前为可离线使用的建议回复";

  const memory = analysis.memory || [];
  const profile = analysis.customer_profile || {};
  document.querySelector("#profileSummary").textContent = profile.summary || "暂未发现需要保留的服务偏好。";
  document.querySelector("#profileTraits").innerHTML = (profile.traits || [])
    .map((item) => `<div class="profile-trait"><small>${escapeHtml(item.label)}</small><strong>${escapeHtml(item.value)}</strong>${sourceButton(item.evidence?.[0], "查看依据")}</div>`)
    .join("") || '<div class="api-empty">本轮暂无明确偏好</div>';
  document.querySelector("#profileScope").textContent = profile.scope || "仅展示与当前服务有关的信息";
  document.querySelector("#journeyTimeline").innerHTML = memory
    .filter((item) => item.kind === "service_event" || item.kind === "commitment")
    .map(
      (item, index) => `<div class="trace-step"><div class="trace-index">${index + 1}</div><div class="trace-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.value)}</span>${sourceButton(item.source_id)}</div></div>`,
    )
    .join("") || '<div class="api-empty">暂无跨系统服务事件</div>';
  document.querySelector("#knownFacts").innerHTML = memory
    .filter((item) => item.kind === "service_fact")
    .map(
      (item) => `<div class="plain-row"><div class="plain-row-icon"><i data-lucide="check"></i></div><div class="plain-row-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.value)}</span>${sourceButton(item.source_id)}</div></div>`,
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

  const followupRequired = analysis.risk_level !== "none" || bundle.commitments.some((item) => item.status !== "已关闭");
  const followupBadge = document.querySelector("#followupBadge");
  followupBadge.textContent = analysis.risk_level === "high" ? "必须" : followupRequired ? "需要" : "无需";
  followupBadge.className = `status-pill${analysis.risk_level === "high" ? " danger" : followupRequired ? " warn" : ""}`;
  const followupSummary = document.querySelector("#followupSummary");
  followupSummary.className = `summary-callout${analysis.risk_level === "high" ? " alert" : ""}`;
  followupSummary.innerHTML = `<i data-lucide="${followupRequired ? "bell-ring" : "circle-check"}"></i><span>${escapeHtml(followupRequired ? "本次服务存在待跟进事项，需要指定负责人并保留处理结果。" : "当前有明确处理方案，暂无需主管介入。")}</span>`;
  [document.querySelector("#createTicket"), document.querySelector("#followupAction")].forEach((button) => {
    button.textContent = followupRequired ? "前往风险台跟进" : "添加跟进提醒";
    button.className = `risk-action${analysis.risk_level === "high" ? "" : " neutral"}`;
  });

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
    document.querySelector("#suggestedReply").textContent = draft.reply_draft;
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
  const tone = tones[state.draftToneIndex % tones.length];
  state.draftToneIndex += 1;
  const sessionId = state.activeId;
  const version = state.activeVersion;
  try {
    const draft = await api(`/api/conversations/${encodeURIComponent(sessionId)}/draft`, {
      method: "POST",
      body: JSON.stringify({ tone }),
    });
    if (state.activeId !== sessionId || state.activeVersion !== version) return;
    state.current.draft = draft;
    document.querySelector("#suggestedReply").textContent = draft.reply_draft;
    document.querySelector("#replyTags").innerHTML = (draft.tags || []).slice(0, 2)
      .map((tag, index) => `<span class="chip${index === 0 ? " blue" : ""}">${escapeHtml(tag)}</span>`)
      .join("");
    document.querySelector("#suggestionSource").textContent = draft.provider === "qwen"
      ? `已结合当前记录生成 · ${draft.latency_ms || 0} ms`
      : "已使用离线备用回复";
    showToast(`已换成「${tone}」说法`);
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(button, false);
  }
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

async function openEvidence(evidenceId) {
  const dialog = document.querySelector("#evidenceDialog");
  document.querySelector("#evidenceTitle").textContent = "正在读取";
  document.querySelector("#evidenceContent").innerHTML = '<div class="loading-block">正在查找原始记录…</div>';
  dialog.showModal();
  try {
    const evidence = await api(`/api/evidence/${encodeURIComponent(evidenceId)}`);
    document.querySelector("#evidenceTitle").textContent = evidence.title;
    document.querySelector("#evidenceContent").innerHTML = `
      ${evidence.media_url ? `<a class="evidence-media" href="${escapeHtml(evidence.media_url)}" target="_blank" rel="noopener"><img src="${escapeHtml(evidence.media_url)}" alt="原始证据图片" width="320" height="220" /><span>打开原图</span></a>` : ""}
      <div class="evidence-quote">${escapeHtml(evidence.content)}</div>
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
  if (state.riskFilter === "high") return items.filter((item) => item.severity === "high");
  if (state.riskFilter === "unassigned") return items.filter((item) => !item.owner);
  return items;
}

function renderRiskKpis() {
  const summary = state.risks?.summary || { open: 0, high: 0, unassigned: 0, commitments: 0 };
  document.querySelector("#riskKpis").innerHTML = [
    ["待处理事件", summary.open, "danger"],
    ["高风险", summary.high, "danger"],
    ["待分配", summary.unassigned, "warning"],
    ["进行中承诺", summary.commitments, ""],
  ]
    .map(([label, value, tone]) => `<div class="risk-kpi ${tone}"><small>${label}</small><strong>${value}</strong></div>`)
    .join("");
}

function renderRiskTable() {
  const items = riskItems();
  document.querySelector("#riskListMeta").textContent = `共 ${items.length} 条，按风险程度排序`;
  const table = document.querySelector("#riskTable");
  if (!items.length) {
    table.innerHTML = '<div class="api-empty">当前筛选下没有待处理事件。</div>';
    return;
  }
  table.innerHTML = `
    <div class="risk-table-head"><span>等级</span><span>事件</span><span>客户 / 场景</span><span>负责人</span><span>截止时间</span></div>
    ${items
      .map(
        (item) => `<button class="risk-row${item.id === state.selectedRiskId ? " selected" : ""}" type="button" data-risk-id="${item.id}">
          <span class="severity ${item.severity}">${item.severity === "high" ? "高" : "中"}</span>
          <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small><small class="risk-compact-meta">${escapeHtml(item.buyer_nickname)} · ${escapeHtml(item.scene_minor)} · ${escapeHtml(item.owner || "待分配")}</small></span>
          <span><strong>${escapeHtml(item.buyer_nickname)}</strong><small>${escapeHtml(item.session_id)} · ${escapeHtml(item.scene_minor)}</small></span>
          <span class="risk-status${item.owner ? "" : " unassigned"}">${escapeHtml(item.owner || "待分配")}</span>
          <span class="risk-status">${escapeHtml(formatTime(item.deadline))}</span>
        </button>`,
      )
      .join("")}`;
}

function renderRiskDetail() {
  const item = (state.risks?.risks || []).find((risk) => risk.id === state.selectedRiskId);
  const target = document.querySelector("#riskDetail");
  if (!item) {
    target.innerHTML = '<div class="empty-detail"><i data-lucide="mouse-pointer-click"></i><strong>选择一条风险事件</strong><span>查看证据、负责人和处理时限</span></div>';
    refreshIcons();
    return;
  }
  target.innerHTML = `
    <div class="risk-detail-head"><span class="severity ${item.severity}">${item.severity === "high" ? "高风险" : "中风险"}</span><h3>${escapeHtml(item.title)}</h3></div>
    <div class="risk-detail-body">
      <section><h4>为什么需要处理</h4><p>${escapeHtml(item.detail)}</p></section>
      <section><h4>原始证据</h4><div class="risk-evidence-list">${(item.evidence || [])
        .map((evidence) => `<button type="button" data-evidence-id="${escapeHtml(evidence.id)}"><strong>${escapeHtml(evidence.label || evidence.source_type)}</strong><span>${escapeHtml(evidence.value)}</span></button>`)
        .join("")}</div></section>
      <section><h4>处理记录</h4>
        <form class="risk-form" id="riskForm">
          <label>负责人<input name="owner" value="${escapeHtml(item.owner || "")}" autocomplete="off" placeholder="例如：林小稚…" /></label>
          <label>处理状态<select name="status"><option ${item.status === "待处理" ? "selected" : ""}>待处理</option><option ${item.status === "处理中" ? "selected" : ""}>处理中</option><option ${item.status === "待回访" ? "selected" : ""}>待回访</option><option ${item.status === "已关闭" ? "selected" : ""}>已关闭</option></select></label>
          <label class="full">截止时间<input name="deadline" type="datetime-local" autocomplete="off" value="${escapeHtml(inputDateTime(item.deadline))}" /></label>
          <button type="submit">保存处理记录</button>
        </form>
      </section>
      <button class="secondary-button" id="openRiskConversation" type="button" data-session-id="${escapeHtml(item.session_id)}">回到原始会话</button>
    </div>`;
  refreshIcons();
}

async function loadRisks() {
  document.querySelector("#riskTable").innerHTML = '<div class="loading-block">正在同步风险事件…</div>';
  try {
    state.risks = await api("/api/risks");
    if (!state.selectedRiskId && state.risks.risks.length) state.selectedRiskId = state.risks.risks[0].id;
    renderRiskKpis();
    renderRiskTable();
    renderRiskDetail();
    document.querySelector("#riskBadge").textContent = String(state.risks.summary.open);
  } catch (error) {
    document.querySelector("#riskTable").innerHTML = `<div class="api-empty">${escapeHtml(error.message)}</div>`;
  }
}

async function saveRisk(form) {
  const data = new FormData(form);
  const payload = {
    owner: String(data.get("owner") || "").trim() || null,
    deadline: String(data.get("deadline") || "").replace("T", " ") || null,
    status: String(data.get("status") || "待处理"),
  };
  const submitButton = form.querySelector('button[type="submit"]');
  setLoading(submitButton, true);
  try {
    await api(`/api/risks/${state.selectedRiskId}`, { method: "PATCH", body: JSON.stringify(payload) });
    showToast("风险处理记录已更新");
    await loadRisks();
  } catch (error) {
    showToast(error.message, "circle-alert");
  } finally {
    setLoading(submitButton, false);
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
    input.value = document.querySelector("#suggestedReply").textContent.trim();
    input.focus();
    document.querySelector("#qualityStrip").hidden = true;
    showToast("建议已放入输入框，可继续修改");
  });
  document.querySelector("#regenerateReply").addEventListener("click", regenerateReply);
  document.querySelector("#sendReply").addEventListener("click", sendReply);
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
  document.querySelector("#backToService").addEventListener("click", () => setView("service"));
  document.querySelector("#refreshRisks").addEventListener("click", loadRisks);
  document.querySelectorAll("[data-risk-filter]").forEach((button) =>
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-risk-filter]").forEach((item) => {
        item.classList.remove("active");
        item.setAttribute("aria-pressed", "false");
      });
      button.classList.add("active");
      button.setAttribute("aria-pressed", "true");
      state.riskFilter = button.dataset.riskFilter;
      renderRiskTable();
      updateRiskUrl();
    }),
  );
  document.querySelector("#riskDetail").addEventListener("submit", (event) => {
    if (event.target.id === "riskForm") {
      event.preventDefault();
      saveRisk(event.target);
    }
  });
  document.querySelector("#riskDetail").addEventListener("click", (event) => {
    const button = event.target.closest("#openRiskConversation");
    if (!button) return;
    setView("service");
    loadConversation(button.dataset.sessionId);
  });
  [document.querySelector("#createTicket"), document.querySelector("#followupAction")].forEach((button) =>
    button.addEventListener("click", () => setView("risk")),
  );
  document.querySelector("#closeConversation").addEventListener("click", () => {
    state.snoozedIds.add(state.activeId);
    document.querySelector('[data-inbox-view="later"]').click();
    showToast("会话已移入稍后处理");
  });
  document.querySelector("#closeEvidence").addEventListener("click", () => document.querySelector("#evidenceDialog").close());
  document.querySelector("#evidenceDialog").addEventListener("click", (event) => {
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
