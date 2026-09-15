"use strict";

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

async function evaluationApi(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("评测服务暂时不可用");
  return response.json();
}

function formatInteger(value) {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value || 0);
}

function systemResultRow(item) {
  return `<div class="result-row">
    <div class="result-icon"><i data-lucide="${item.passed ? "check" : "x"}"></i></div>
    <div class="result-copy"><strong>${escapeEvaluationHtml(item.name)}</strong><span>${escapeEvaluationHtml(item.expected)}</span></div>
    <span class="case-id">${escapeEvaluationHtml(item.dimension)} · ${escapeEvaluationHtml(item.case)}</span>
    <span class="result-state">${item.passed ? "通过" : "未通过"}</span>
  </div>`;
}

function modelResultRow(item) {
  return `<div class="result-row">
    <div class="result-icon"><i data-lucide="${item.passed ? "check" : "x"}"></i></div>
    <div class="result-copy"><strong>${escapeEvaluationHtml(item.text)}</strong><span>预期：${escapeEvaluationHtml(item.expected)} · 实际：${escapeEvaluationHtml(item.actual)}</span></div>
    <span class="case-id">Qwen 实测</span>
    <span class="result-state">${item.passed ? "通过" : "未通过"}</span>
  </div>`;
}

function renderEvaluation(health, evaluation) {
  const modelEvaluation = evaluation.model_evaluation || {};
  const run = modelEvaluation.available && modelEvaluation.model ? modelEvaluation : evaluation.latest_qwen_run;
  const dimensions = evaluation.dimensions || {};
  const dimensionTotal = (...names) => names.reduce((sum, name) => sum + (dimensions[name]?.total || 0), 0);
  const dimensionPassed = (...names) => names.reduce((sum, name) => sum + (dimensions[name]?.passed || 0), 0);
  document.querySelector("#evaluationModel").innerHTML = `<i data-lucide="circle"></i>${health.ai.configured ? "Qwen 在线" : "本地保障"}`;
  document.querySelector("#metricPass").textContent = `${evaluation.suite.passed}/${evaluation.suite.total}`;
  document.querySelector("#metricPassMeta").textContent = `${Math.round(evaluation.suite.pass_rate * 100)}% 场景通过`;
  document.querySelector("#metricUnderstanding").textContent = modelEvaluation.available
    ? `${modelEvaluation.passed}/${modelEvaluation.total}`
    : "—";
  document.querySelector("#metricUnderstandingMeta").textContent = modelEvaluation.available
    ? `${escapeEvaluationHtml(modelEvaluation.model || health.ai.text_model)} · ${modelEvaluation.cached ? "缓存结果" : "本次实测"}`
    : "未配置模型";
  document.querySelector("#metricSafety").textContent = `${dimensionPassed("风险识别", "回复安全")}/${dimensionTotal("风险识别", "回复安全")}`;
  document.querySelector("#metricTokens").textContent = run ? formatInteger((run.input_tokens || 0) + (run.output_tokens || 0)) : "—";
  document.querySelector("#metricRunMeta").textContent = run ? `${(run.latency_ms / 1000).toFixed(1)} 秒 · 输入 + 输出` : "等待 Qwen 真实返回";
  const modelPassed = !modelEvaluation.available || modelEvaluation.passed === modelEvaluation.total;
  document.querySelector("#suiteBadge").textContent = evaluation.suite.passed === evaluation.suite.total && modelPassed ? "全部通过" : "需要复核";
  document.querySelector("#methodNote").textContent = `${evaluation.suite.note} Qwen 指标来自独立批量调用，不与本地规则分数混算。`;

  const featuredSystemNames = new Set(["跨系统色号冲突", "已上传图片不重复索要", "用户确认后动态完成"]);
  const featuredModelIds = new Set(["qwen_negation", "qwen_ambiguity", "qwen_handoff"]);
  const systemCases = evaluation.cases || [];
  const modelCases = modelEvaluation.cases || [];
  const featuredSystem = systemCases.filter((item) => featuredSystemNames.has(item.name) || !item.passed);
  const featuredModel = modelCases.filter((item) => featuredModelIds.has(item.id) || !item.passed);
  const remainingSystem = systemCases.filter((item) => !featuredSystem.includes(item));
  const remainingModel = modelCases.filter((item) => !featuredModel.includes(item));
  const featuredRows = featuredSystem.map(systemResultRow).join("") + featuredModel.map(modelResultRow).join("");
  const remainingRows = remainingSystem.map(systemResultRow).join("") + remainingModel.map(modelResultRow).join("");
  const remainingCount = remainingSystem.length + remainingModel.length;
  document.querySelector("#evaluationCases").innerHTML = `${featuredRows}
    ${remainingCount ? `<details class="all-results"><summary>查看其余 ${formatInteger(remainingCount)} 项测试明细</summary><div class="all-results-list">${remainingRows}</div></details>` : ""}`;

  const controls = [
    ["分析结果缓存", evaluation.cost_controls.analysis_cache ? "已开启" : "未开启"],
    ["同会话请求合并", evaluation.cost_controls.singleflight_per_session ? "已开启" : "未开启"],
    ["回复生成去重", evaluation.cost_controls.draft_deduplication ? "已开启" : "未开启"],
    ["上下文压缩", evaluation.cost_controls.context_strategy],
    ["按需模型路由", evaluation.cost_controls.on_demand_model_routing ? "已开启" : "未开启"],
    ["文本 / 图片模型分工", evaluation.cost_controls.model_split],
    ["歧义复核最小上下文", evaluation.cost_controls.intent_review_context],
    ["图文任务并行", evaluation.cost_controls.parallel_multimodal ? "已开启" : "未开启"],
    ["AI 首次回复快速通道", evaluation.cost_controls.fast_auto_reply ? "已开启" : "未开启"],
  ];
  document.querySelector("#costControls").innerHTML = controls.map(([name, detail]) => `
    <div class="control-item"><i data-lucide="circle-check"></i><div><strong>${escapeEvaluationHtml(name)}</strong><span>${escapeEvaluationHtml(detail)}</span></div></div>`).join("");

  const trace = evaluation.architecture?.sample_trace || {};
  const steps = trace.steps || [];
  document.querySelector("#routeBadge").textContent = `${steps.length} 个实际节点 · ${Number(trace.total_latency_ms || 0).toFixed(1)} ms`;
  document.querySelector("#skillTrace").innerHTML = steps.map((step) => {
    const metadata = step.metadata || {};
    const tokenCount = (metadata.input_tokens || 0) + (metadata.output_tokens || 0);
    const executionMeta = [step.engine, metadata.model, `${Number(step.latency_ms || 0).toFixed(1)} ms`, tokenCount ? `${tokenCount} tokens` : ""]
      .filter(Boolean)
      .join(" · ");
    const detail = [
      step.reason,
      step.input_summary ? `输入：${step.input_summary}` : "",
      step.output_summary ? `输出：${step.output_summary}` : "",
    ].filter(Boolean).join("；");
    return `
    <div class="skill-item ${step.status}">
      <i data-lucide="${step.status === "failed" ? "triangle-alert" : "check"}"></i>
      <div><strong>${escapeEvaluationHtml(step.label)}</strong><span>${escapeEvaluationHtml(detail)}</span></div>
      <small>${escapeEvaluationHtml(executionMeta)}</small>
    </div>`;
  }).join("") || '<div class="empty-rag">暂无执行轨迹</div>';
  const transitions = (trace.state_transitions || []).map((item) => item.to).join(" → ");
  const handoff = trace.human_handoff?.required
    ? `需人工接管：${trace.human_handoff.reason}`
    : "本轮无需人工接管";
  document.querySelector("#traceState").textContent = [transitions ? `状态：${transitions}` : "", handoff].filter(Boolean).join("；");

  document.querySelector("#ragMethod").textContent = evaluation.rag
    ? `${evaluation.rag.method} · ${evaluation.rag.latency_ms.toFixed(1)} ms`
    : "暂无检索数据";
  document.querySelector("#ragEvidence").innerHTML = (evaluation.rag?.retrieved || []).map((item) => `
    <div class="rag-item">
      <div><strong>${escapeEvaluationHtml(item.document_name)}</strong><span>${escapeEvaluationHtml(item.source_type)}</span></div>
      <small>BM25 ${item.retrieval.bm25.toFixed(2)} · 向量 ${item.retrieval.vector.toFixed(2)}</small>
    </div>`).join("") || '<div class="empty-rag">本轮没有召回知识</div>';
  evaluationIcons();
}

async function loadEvaluation() {
  const button = document.querySelector("#refreshEvaluation");
  button.disabled = true;
  button.classList.add("busy");
  try {
    const [health, evaluation] = await Promise.all([evaluationApi("/api/health"), evaluationApi("/api/evaluation")]);
    renderEvaluation(health, evaluation);
  } catch (error) {
    document.querySelector("#evaluationCases").innerHTML = `<div class="loading-state">${escapeEvaluationHtml(error.message)}，请刷新重试。</div>`;
  } finally {
    button.disabled = false;
    button.classList.remove("busy");
  }
}

document.querySelector("#refreshEvaluation").addEventListener("click", loadEvaluation);
evaluationIcons();
loadEvaluation();
