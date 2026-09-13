"use strict";

const consumerState = {
  sessionId: new URL(window.location.href).searchParams.get("case") || "S00018",
  data: null,
  version: null,
  loading: false,
  pendingImage: null,
};

const consumerTime = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const consumerCurrency = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});
const phoneClockFormatter = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function updatePhoneClock() {
  document.querySelector("#phoneClock").textContent = phoneClockFormatter.format(new Date());
}

function escapeConsumerHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function refreshConsumerIcons() {
  if (!window.lucide) return;
  window.lucide.createIcons();
  document.querySelectorAll("svg.lucide").forEach((icon) => {
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
  });
}

async function consumerApi(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "服务暂时不可用，请稍后重试");
  }
  return data;
}

function formatConsumerTime(value) {
  if (!value) return "";
  const date = new Date(value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? value : consumerTime.format(date);
}

function consumerVersion(data) {
  const messages = data?.messages || [];
  const last = messages[messages.length - 1];
  return last ? `${last.message_id}:${last.message_seq}` : "empty";
}

function showConsumerToast(text) {
  const toast = document.querySelector("#consumerToast");
  document.querySelector("#consumerToastText").textContent = text;
  toast.classList.add("show");
  clearTimeout(showConsumerToast.timer);
  showConsumerToast.timer = setTimeout(() => toast.classList.remove("show"), 2400);
}

function renderConsumerOrder(order) {
  const target = document.querySelector("#consumerOrder");
  if (!order) {
    target.innerHTML = '<div class="empty-state">本次咨询暂无关联订单</div>';
    return;
  }
  target.innerHTML = `
    <div class="order-product">
      <div class="product-visual">${escapeConsumerHtml((order.sku || "商品").slice(-3))}</div>
      <div><strong>${escapeConsumerHtml(order.product_name || "订单商品")}</strong><span>实付 ${order.paid_amount == null ? "--" : escapeConsumerHtml(consumerCurrency.format(order.paid_amount))}</span></div>
    </div>
    <div class="order-meta"><span>订单 ${escapeConsumerHtml(order.order_id)}</span><strong>${escapeConsumerHtml(order.status || "处理中")}</strong></div>`;
}

function renderConsumerMessages(messages, { announce = false } = {}) {
  const feed = document.querySelector("#consumerMessages");
  if (!messages.length) {
    feed.innerHTML = '<div class="empty-state">发送一条消息开始咨询</div>';
    return;
  }
  feed.innerHTML = `<div class="message-day">本次服务记录</div>${messages
    .map((message) => {
      const customer = message.role === "customer";
      const body = message.content_type === "image"
        ? message.image_url
          ? `<span class="message-photo-wrap"><img src="${escapeConsumerHtml(message.image_url)}" alt="用户发送的服务图片" loading="lazy" /><span class="message-photo-caption">${escapeConsumerHtml(message.text || "请帮我看一下这张图片。")}</span></span>`
          : '<span class="message-image"><i data-lucide="image"></i><span>图片记录（原图未提供）</span></span>'
        : escapeConsumerHtml(message.text);
      return `<div class="consumer-message ${customer ? "customer" : "agent"}">
        ${customer ? "" : '<div class="message-avatar">林</div>'}
        <div class="message-body"><div class="message-bubble">${body}</div><div class="message-time">${escapeConsumerHtml(formatConsumerTime(message.sent_at))}</div></div>
      </div>`;
    })
    .join("")}`;
  feed.scrollTop = feed.scrollHeight;
  refreshConsumerIcons();
  if (announce) showConsumerToast("客服发来了新回复");
}

function renderConsumer(data, options = {}) {
  consumerState.data = data;
  consumerState.version = consumerVersion(data);
  document.querySelector("#caseTitle").textContent = data.conversation.scene_minor || "售后咨询";
  document.querySelector("#customerName").textContent = `${data.conversation.buyer_nickname} · 服务单 ${data.conversation.session_id}`;
  document.querySelector("#syncState").textContent = "已连接";
  renderConsumerOrder(data.order);
  renderConsumerMessages(data.messages, options);
}

async function loadConsumerConversation() {
  try {
    const data = await consumerApi(`/api/consumer/conversations/${encodeURIComponent(consumerState.sessionId)}`);
    renderConsumer(data);
  } catch (error) {
    document.querySelector("#syncState").textContent = "连接失败";
    document.querySelector("#consumerMessages").innerHTML = `<div class="empty-state">${escapeConsumerHtml(error.message)}</div>`;
  }
}

async function sendConsumerMessage() {
  const input = document.querySelector("#consumerInput");
  const button = document.querySelector("#consumerSend");
  const delivery = document.querySelector("#deliveryState");
  const text = input.value.trim();
  const pendingImage = consumerState.pendingImage;
  if ((!text && !pendingImage) || consumerState.loading) {
    if (!text && !pendingImage) {
      delivery.textContent = "请输入文字或选择图片";
      input.focus();
    }
    return;
  }

  consumerState.loading = true;
  button.disabled = true;
  button.classList.add("busy");
  button.setAttribute("aria-busy", "true");
  delivery.textContent = "正在发送…";
  try {
    const result = await consumerApi(
      `/api/consumer/conversations/${encodeURIComponent(consumerState.sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({
          text,
          image_data_url: pendingImage?.dataUrl || null,
          image_name: pendingImage?.name || null,
        }),
      },
    );
    input.value = "";
    clearConsumerImage();
    renderConsumer(result.conversation);
    delivery.textContent = pendingImage ? "图片已送达，正在核对" : "已送达，助手正在理解";
    showConsumerToast(pendingImage ? "图片已送达客服" : "消息已送达");
  } catch (error) {
    delivery.textContent = `${error.message}，请重新发送`;
    input.focus();
  } finally {
    consumerState.loading = false;
    button.disabled = false;
    button.classList.remove("busy");
    button.removeAttribute("aria-busy");
  }
}

function readConsumerFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("图片读取失败"));
    reader.readAsDataURL(file);
  });
}

async function prepareConsumerImage(file) {
  if (!file || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error("请选择 JPG、PNG 或 WebP 图片");
  }
  if (file.size > 15 * 1024 * 1024) throw new Error("原图请小于 15 MB");
  if (!("createImageBitmap" in window)) {
    if (file.size > 4 * 1024 * 1024) throw new Error("图片请小于 4 MB");
    return readConsumerFile(file);
  }

  const bitmap = await createImageBitmap(file);
  const longest = Math.max(bitmap.width, bitmap.height);
  const scale = Math.min(1, 1600 / longest);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  const context = canvas.getContext("2d");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.86));
  if (!blob || blob.size > 4 * 1024 * 1024) throw new Error("图片压缩失败，请换一张图片");
  return readConsumerFile(blob);
}

function clearConsumerImage() {
  consumerState.pendingImage = null;
  const preview = document.querySelector("#attachmentPreview");
  preview.hidden = true;
  document.querySelector("#consumerComposer").classList.remove("has-attachment");
  document.querySelector("#consumerImageInput").value = "";
}

async function selectConsumerImage(file) {
  const delivery = document.querySelector("#deliveryState");
  delivery.textContent = "正在准备图片…";
  try {
    const dataUrl = await prepareConsumerImage(file);
    consumerState.pendingImage = { dataUrl, name: file.name || "现场照片" };
    document.querySelector("#attachmentThumbnail").src = dataUrl;
    document.querySelector("#attachmentName").textContent = file.name || "现场照片";
    document.querySelector("#attachmentPreview").hidden = false;
    document.querySelector("#consumerComposer").classList.add("has-attachment");
    delivery.textContent = "可补充说明后发送";
    refreshConsumerIcons();
  } catch (error) {
    clearConsumerImage();
    delivery.textContent = error.message;
  }
}

async function pollConsumerConversation() {
  if (document.hidden || consumerState.loading || !consumerState.data) return;
  try {
    const data = await consumerApi(`/api/consumer/conversations/${encodeURIComponent(consumerState.sessionId)}`);
    const nextVersion = consumerVersion(data);
    if (nextVersion === consumerState.version) return;
    const previousMessages = consumerState.data.messages || [];
    const nextMessages = data.messages || [];
    const receivedAgentReply = nextMessages.length > previousMessages.length && nextMessages[nextMessages.length - 1]?.role === "agent";
    renderConsumer(data, { announce: receivedAgentReply });
  } catch {
    document.querySelector("#syncState").textContent = "正在重连…";
  }
}

function setupConsumerEvents() {
  document.querySelector("#consumerComposer").addEventListener("submit", (event) => {
    event.preventDefault();
    sendConsumerMessage();
  });
  document.querySelector("#consumerInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendConsumerMessage();
    }
  });
  ["#consumerImage", "#consumerFile"].forEach((selector) => {
    document.querySelector(selector).addEventListener("click", () => document.querySelector("#consumerImageInput").click());
  });
  document.querySelector("#consumerImageInput").addEventListener("change", (event) => selectConsumerImage(event.target.files?.[0]));
  document.querySelector("#removeAttachment").addEventListener("click", () => {
    clearConsumerImage();
    document.querySelector("#deliveryState").textContent = "图片已移除";
  });
  document.querySelector("#chatInfo").addEventListener("click", () => showConsumerToast(`服务单 ${consumerState.sessionId}`));
  document.querySelector("#consumerBack").addEventListener("click", () => showConsumerToast("当前已是演示会话"));
}

setupConsumerEvents();
updatePhoneClock();
refreshConsumerIcons();
loadConsumerConversation();
setInterval(pollConsumerConversation, 1200);
setInterval(updatePhoneClock, 30000);
