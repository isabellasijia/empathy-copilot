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

function renderEvaluation(health, evaluation) {
  const run = evaluation.latest_qwen_run;
  const dimensions = evaluation.dimensions || {};
  const dimensionTotal = (...names) => names.reduce((sum, name) => sum + (dimensions[name]?.total || 0), 0);
  const dimensionPassed = (...names) => names.reduce((sum, name) => sum + (dimensions[name]?.passed || 0), 0);
  document.querySelector("#evaluationModel").innerHTML = `<i data-lucide="circle"></i>${health.ai.configured ? "Qwen 在线" : "本地保障"}`;
  document.querySelector("#metricPass").textContent = `${evaluation.suite.passed}/${evaluation.suite.total}`;
  document.querySelector("#metricPassMeta").textContent = `${Math.round(evaluation.suite.pass_rate * 100)}% 场景通过`;
  document.querySelector("#metricUnderstanding").textContent = `${dimensionPassed("意图识别", "情绪判断")}/${dimensionTotal("意图识别", "情绪判断")}`;
  document.querySelector("#metricSafety").textContent = `${dimensionPassed("风险识别", "回复安全")}/${dimensionTotal("风险识别", "回复安全")}`;
  document.querySelector("#metricTokens").textContent = run ? formatInteger((run.input_tokens || 0) + (run.output_tokens || 0)) : "—";
  document.querySelector("#metricRunMeta").textContent = run ? `${(run.latency_ms / 1000).toFixed(1)} 秒 · 输入 + 输出` : "等待 Qwen 真实返回";
  document.querySelector("#suiteBadge").textContent = evaluation.suite.passed === evaluation.suite.total ? "全部通过" : "需要修复";
  document.querySelector("#methodNote").textContent = evaluation.suite.note;

  document.querySelector("#evaluationCases").innerHTML = evaluation.cases.map((item) => `
    <div class="result-row">
      <div class="result-icon"><i data-lucide="${item.passed ? "check" : "x"}"></i></div>
      <div class="result-copy"><strong>${escapeEvaluationHtml(item.name)}</strong><span>${escapeEvaluationHtml(item.expected)}</span></div>
      <span class="case-id">${escapeEvaluationHtml(item.dimension)} · ${escapeEvaluationHtml(item.case)}</span>
      <span class="result-state">${item.passed ? "通过" : "未通过"}</span>
    </div>`).join("");

  const controls = [
    ["分析结果缓存", evaluation.cost_controls.analysis_cache ? "已开启" : "未开启"],
    ["同会话请求合并", evaluation.cost_controls.singleflight_per_session ? "已开启" : "未开启"],
    ["回复生成去重", evaluation.cost_controls.draft_deduplication ? "已开启" : "未开启"],
    ["上下文压缩", evaluation.cost_controls.context_strategy],
    ["按需模型路由", evaluation.cost_controls.on_demand_model_routing ? "已开启" : "未开启"],
  ];
  document.querySelector("#costControls").innerHTML = controls.map(([name, detail]) => `
    <div class="control-item"><i data-lucide="circle-check"></i><div><strong>${escapeEvaluationHtml(name)}</strong><span>${escapeEvaluationHtml(detail)}</span></div></div>`).join("");

  const trace = evaluation.architecture?.sample_trace || {};
  document.querySelector("#routeBadge").textContent = `${trace.called_skills || 0} 调用 · ${trace.skipped_skills || 0} 跳过`;
  document.querySelector("#skillTrace").innerHTML = (trace.skills || []).map((skill) => `
    <div class="skill-item ${skill.status}">
      <i data-lucide="${skill.status === "called" ? "check" : "minus"}"></i>
      <div><strong>${escapeEvaluationHtml(skill.label)}</strong><span>${escapeEvaluationHtml(skill.reason)}</span></div>
      <small>${skill.status === "called" ? "已调用" : "已跳过"}</small>
    </div>`).join("");

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
