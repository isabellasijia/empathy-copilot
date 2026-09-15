"use strict";

const numberFormatter = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const decimalFormatter = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const timeFormatter = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

function escapeEvaluationHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function evaluationIcons() {
  if (!window.lucide) return;
  window.lucide.createIcons();
  document.querySelectorAll("svg.lucide").forEach((icon) => {
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
  });
}

async function evaluationApi() {
  const response = await fetch("/api/evaluation");
  if (!response.ok) throw new Error("评测服务暂时不可用");
  return response.json();
}

function formatInteger(value) {
  return numberFormatter.format(Number(value) || 0);
}

function formatLatency(value) {
  const latency = Number(value) || 0;
  return latency >= 1000 ? `${decimalFormatter.format(latency / 1000)} 秒` : `${formatInteger(latency)} ms`;
}

function renderDimensions(dimensions) {
  const entries = Object.entries(dimensions || {});
  document.querySelector("#dimensionSummary").textContent = `${formatInteger(entries.length)} 个关键维度`;
  document.querySelector("#dimensionChart").innerHTML = entries.map(([name, result]) => {
    const total = Number(result.total) || 0;
    const passed = Number(result.passed) || 0;
    const percent = total ? Math.round((passed / total) * 100) : 0;
    return `<div class="dimension-row">
      <span title="${escapeEvaluationHtml(name)}">${escapeEvaluationHtml(name)}</span>
      <div class="dimension-bar" role="img" aria-label="${escapeEvaluationHtml(name)}通过率 ${percent}%"><i style="--score: ${percent}%"></i></div>
      <strong>${formatInteger(passed)}/${formatInteger(total)}</strong>
    </div>`;
  }).join("") || '<div class="loading-state">暂无维度数据</div>';
}

function renderScenarios(evaluation) {
  const featuredIds = ["cross_system_conflict", "emotion_recovery", "intent_ambiguity_fallback", "human_handoff"];
  const cases = evaluation.cases || [];
  const featured = featuredIds.map((id) => cases.find((item) => item.id === id)).filter(Boolean);
  const fallback = cases.filter((item) => !featured.includes(item)).slice(0, Math.max(0, 4 - featured.length));
  const visibleCases = [...featured, ...fallback].slice(0, 4);
  document.querySelector("#evaluationCases").innerHTML = visibleCases.map((item) => `
    <div class="scenario-row ${item.passed ? "" : "failed"}">
      <div class="scenario-icon"><i data-lucide="${item.passed ? "check" : "x"}"></i></div>
      <div class="scenario-copy">
        <strong>${escapeEvaluationHtml(item.name)}</strong>
        <span>${escapeEvaluationHtml(item.expected)}</span>
      </div>
      <span class="scenario-state">${item.passed ? "通过" : "需复核"}</span>
    </div>`).join("") || '<div class="loading-state">暂无场景数据</div>';
}

function renderEvidence(evaluation, trace) {
  const controls = evaluation.cost_controls || {};
  const originalMessages = Number(trace.features?.message_count) || 0;
  const compressedMessages = Number(controls.max_chat_messages_per_analysis) || 0;
  const calledNodes = (trace.steps || []).filter((step) => step.engine && step.engine !== "local").length;
  const rag = evaluation.rag || {};
  const evidence = [
    ["上下文压缩", originalMessages && compressedMessages ? `${originalMessages} → ${compressedMessages} 条` : "按需裁剪", "保留近期对话与业务证据"],
    ["模型按需调用", `${formatInteger(calledNodes)} 个节点`, controls.on_demand_model_routing ? "仅复杂场景进入深度分析" : "按固定流程分析"],
    ["结果复用", controls.analysis_cache ? "已启用" : "未启用", "相同消息版本不重复消耗"],
    ["检索耗时", formatLatency(rag.latency_ms), "关键词 + 语义混排"],
  ];
  document.querySelector("#costEvidence").innerHTML = evidence.map(([name, value, note]) => `
    <div><dt>${escapeEvaluationHtml(name)}</dt><dd>${escapeEvaluationHtml(value)}</dd><small>${escapeEvaluationHtml(note)}</small></div>`).join("");

  const topKnowledge = (rag.retrieved || []).slice(0, 2);
  const knowledgeNames = topKnowledge.map((item) => item.document_name).join("、");
  document.querySelector("#knowledgeEvidence").innerHTML = `
    <i data-lucide="library"></i>
    <div><strong>知识证据已命中 ${formatInteger(topKnowledge.length)} 条</strong><span>${escapeEvaluationHtml(knowledgeNames || "本轮无需调用知识库")}</span></div>
    <small>${escapeEvaluationHtml(rag.graph_scope ? "业务关系联查" : "混排召回")}</small>`;
}

function engineLabel(engine) {
  if (engine === "local") return "本地计算";
  if (engine === "qwen-text") return "文本模型";
  if (engine === "qwen-omni") return "多模态模型";
  return "智能分析";
}

function renderWorkflow(trace) {
  const steps = (trace.steps || []).slice(0, 7);
  const track = document.querySelector("#workflowTrack");
  track.style.setProperty("--step-count", Math.max(steps.length, 1));
  track.innerHTML = steps.map((step) => {
    const metadata = step.metadata || {};
    const tokens = (Number(metadata.input_tokens) || 0) + (Number(metadata.output_tokens) || 0);
    const details = [engineLabel(step.engine), formatLatency(step.latency_ms), tokens ? `${formatInteger(tokens)} Token` : ""].filter(Boolean).join(" · ");
    return `<li class="workflow-step"><strong title="${escapeEvaluationHtml(step.label)}">${escapeEvaluationHtml(step.label)}</strong><span>${escapeEvaluationHtml(details)}</span></li>`;
  }).join("") || '<li class="loading-state">暂无执行链路</li>';
  document.querySelector("#routeBadge").textContent = `${formatInteger(steps.length)} 个节点 · ${formatLatency(trace.total_latency_ms)}`;

  const handoff = trace.human_handoff || {};
  const finalCopy = handoff.required ? `已转人工：${handoff.reason || "需要人工处理"}` : "流程完成，本轮无需人工接管";
  document.querySelector("#workflowResult").innerHTML = `<strong>${escapeEvaluationHtml(finalCopy)}</strong>`;
}

function renderEvaluation(evaluation) {
  const suite = evaluation.suite || {};
  const modelEvaluation = evaluation.model_evaluation || {};
  const trace = evaluation.architecture?.sample_trace || {};
  const run = modelEvaluation.available ? modelEvaluation : evaluation.latest_qwen_run;
  const dimensions = evaluation.dimensions || {};
  const safetyNames = ["风险识别", "回复安全"];
  const safetyPassed = safetyNames.reduce((sum, name) => sum + (Number(dimensions[name]?.passed) || 0), 0);
  const safetyTotal = safetyNames.reduce((sum, name) => sum + (Number(dimensions[name]?.total) || 0), 0);
  const passRate = Math.round((Number(suite.pass_rate) || 0) * 100);

  document.querySelector("#metricPassRate").textContent = `${passRate}%`;
  document.querySelector("#metricPassMeta").textContent = `${formatInteger(suite.passed)}/${formatInteger(suite.total)} 场景通过`;
  document.querySelector("#metricUnderstanding").textContent = modelEvaluation.available ? `${formatInteger(modelEvaluation.passed)}/${formatInteger(modelEvaluation.total)}` : "—";
  document.querySelector("#metricSafety").textContent = `${formatInteger(safetyPassed)}/${formatInteger(safetyTotal)}`;
  document.querySelector("#metricLatency").textContent = run ? formatLatency(run.latency_ms) : formatLatency(trace.total_latency_ms);
  document.querySelector("#metricTokens").textContent = run ? formatInteger((Number(run.input_tokens) || 0) + (Number(run.output_tokens) || 0)) : "—";

  const allPassed = Number(suite.passed) === Number(suite.total) && (!modelEvaluation.available || Number(modelEvaluation.passed) === Number(modelEvaluation.total));
  const badge = document.querySelector("#suiteBadge");
  badge.textContent = allPassed ? "全部通过" : "需要复核";
  badge.classList.toggle("failed", !allPassed);
  document.querySelector("#evaluationTitle").textContent = allPassed ? "关键场景全部通过" : "部分场景需要复核";

  renderDimensions(dimensions);
  renderScenarios(evaluation);
  renderEvidence(evaluation, trace);
  renderWorkflow(trace);
  document.querySelector("#updatedAt").textContent = `${timeFormatter.format(new Date())} 更新`;
  evaluationIcons();
}

async function loadEvaluation() {
  const button = document.querySelector("#refreshEvaluation");
  button.disabled = true;
  button.classList.add("busy");
  button.setAttribute("aria-busy", "true");
  try {
    renderEvaluation(await evaluationApi());
  } catch (error) {
    document.querySelector("#evaluationCases").innerHTML = `<div class="loading-state error-state">${escapeEvaluationHtml(error.message)}，请刷新重试。</div>`;
    document.querySelector("#updatedAt").textContent = "加载失败";
  } finally {
    button.disabled = false;
    button.classList.remove("busy");
    button.removeAttribute("aria-busy");
  }
}

document.querySelector("#refreshEvaluation").addEventListener("click", loadEvaluation);
evaluationIcons();
loadEvaluation();
