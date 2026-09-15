from __future__ import annotations

import json
import re
import time
from typing import Any

from openai import OpenAI

from .config import Settings
from .intents import intent_catalog


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif hasattr(item, "text"):
                parts.append(str(item.text))
        return "".join(parts)
    return str(content or "")


class QwenService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = (
            OpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.dashscope_base_url,
                timeout=settings.qwen_timeout_seconds,
                max_retries=0,
            )
            if settings.dashscope_api_key
            else None
        )

    @property
    def configured(self) -> bool:
        return self.client is not None

    def warmup_text_model(self) -> None:
        if not self.client:
            return
        try:
            self._request_json(
                '只输出 JSON：{"ready":true}',
                {"task": "warmup"},
                max_tokens=20,
                model=self.settings.qwen_text_model,
            )
        except Exception:
            # Warmup is an optional latency optimization and must not block startup.
            return

    def _request_json(
        self,
        system: str,
        payload: dict[str, Any],
        max_tokens: int = 1800,
        image_urls: list[str] | None = None,
        model: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.client:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        started = time.perf_counter()
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
        user_content: str | list[dict[str, Any]] = serialized
        if image_urls:
            user_content = [
                {"type": "image_url", "image_url": {"url": image_url}}
                for image_url in image_urls
            ]
            user_content.append({"type": "text", "text": serialized})
        selected_model = model or (
            self.settings.qwen_omni_model
            if image_urls
            else self.settings.qwen_text_model
        )
        response = self.client.chat.completions.create(
            model=selected_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        content = _message_text(response.choices[0].message.content)
        result = _json_from_text(content)
        usage = getattr(response, "usage", None)
        metadata = {
            "provider": "qwen",
            "model": selected_model,
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "latency_ms": latency_ms,
        }
        return result, metadata

    def review_intent(
        self,
        latest_message: dict[str, Any],
        previous_customer_message: dict[str, Any] | None,
        deterministic_intent: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        system = """
复核美妆客服最新消息的意图，只输出 JSON。历史只用于理解省略指代，注意否定和转折。
从 candidates 选一个；多个平行诉求无先后或无法判断时，不得猜测，返回待确认并简短追问。
格式：{"primary_intent":{"value":"","category":"","confidence":0.0,"requires_clarification":false,"clarification_question":""}}
待确认时 value=需要进一步确认、category=待确认、requires_clarification=true；否则不得输出候选外的意图。
""".strip()
        candidates = [
            {"value": item.get("value"), "category": item.get("category")}
            for item in deterministic_intent.get("candidates", [])[:3]
        ]
        payload = {
            "latest_message": {
                "message_id": latest_message.get("message_id"),
                "text": latest_message.get("text") or "",
            },
            "previous_customer_message": (
                {
                    "message_id": previous_customer_message.get("message_id"),
                    "text": previous_customer_message.get("text") or "",
                }
                if previous_customer_message
                else None
            ),
            "candidates": candidates,
        }
        result, metadata = self._request_json(system, payload, max_tokens=120)
        intent = result.get("primary_intent") or {}
        requires_clarification = bool(intent.get("requires_clarification"))
        selected = (intent.get("value"), intent.get("category"))
        allowed = {(item["value"], item["category"]) for item in candidates}
        if requires_clarification:
            intent["value"] = "需要进一步确认"
            intent["category"] = "待确认"
            intent["confidence"] = min(float(intent.get("confidence") or 0.5), 0.69)
        elif selected not in allowed:
            intent = dict(deterministic_intent)
        intent["evidence"] = (
            [str(latest_message["message_id"])]
            if latest_message.get("message_id")
            else []
        )
        intent["slots"] = deterministic_intent.get("slots") or []
        result["primary_intent"] = intent
        metadata["task"] = "intent_review"
        return result, metadata

    def evaluate_intents(
        self, cases: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        system = """
你是美妆电商客服意图分类评测器。每条输入相互独立，只输出 JSON。
识别否定、转折、服务完成、问题未解决和转人工。多个平行诉求没有先后，或请求超出客服范围时，输出待确认，不得猜测。
“不想 A、只想 B”或“不是 A、而是 B”已经明确排除了 A，应选择 B，不属于多意图歧义。
标签边界：商品本身漏液、碎裂、压坏属于“处理商品破损”；收到错误商品或数量缺少才属于“处理错发漏发”。
输出：{"results":[{"id":"","value":"","category":"","requires_clarification":false}]}。
value 和 category 必须来自 intent_catalog；无法判断时 value=需要进一步确认、category=待确认、requires_clarification=true。
""".strip()
        result, metadata = self._request_json(
            system,
            {
                "cases": cases,
                "intent_catalog": [
                    *intent_catalog(),
                    {"category": "转人工", "value": "需要人工帮助"},
                    {"category": "服务确认", "value": "确认问题已解决"},
                    {"category": "待确认", "value": "需要进一步确认"},
                ],
            },
            max_tokens=700,
            model=self.settings.qwen_text_model,
        )
        if isinstance(result, list):
            result = {"results": result}
        return result, metadata

    def analyze(
        self,
        context: dict[str, Any],
        deterministic: dict[str, Any],
        image_urls: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        system = """
你是美妆电商人工客服的状态分析器。你只做理解和建议，不执行退款、发货、关单或医疗诊断。
根据聊天、订单、工单和规则分析当前状态。只输出 JSON，不要 Markdown。
判断情绪时优先看用户最新一条消息；用户明确表示满意、开心或感谢时，应识别为情绪已经缓和，不能被更早的负面消息覆盖。
意图判断必须先看最新用户消息，历史消息只用于理解“这个、那、还要多久”等省略表达。识别否定和转折，例如“不想退款，只想换货”应判为申请退换货。
primary_intent 只能从 intent_catalog 中选择；都不符合或两个候选无法区分时，输出 {"value":"需要进一步确认","category":"待确认","confidence":0.35,"requires_clarification":true}。
不要把情绪词当成唯一业务意图：“我很生气，退款还没到账”的主意图是查询退款进度，情绪另行记录。
面向一线客服使用简单、直接的中文：summary 不超过 60 字，next_actions 最多 2 条，每条 detail 不超过 30 字，不使用算法或技术术语。
所有 evidence 只能引用输入中真实存在的 message_id、order_id 或 ticket_id。
不得覆盖 rules_analysis 中的确定性冲突，不得自行编造业务事实、时效和承诺。
输出字段：primary_intent, secondary_intents, service_stage, emotion_state, summary, next_actions, visual_observations。
primary_intent 为 {value, category, confidence, evidence, requires_clarification, slots}；secondary_intents 为数组；
slots 只抽取最新消息中明确出现的业务参数，格式为 [{type, value}]，例如 shade、order_id、tracking_no，不得补全或猜测。
service_stage 为 {value, confidence, evidence}；emotion_state 为 {value, trend, confidence, evidence}；
next_actions 为最多 2 条 {title, detail, priority}，priority 只能是 high 或 normal。
visual_observations 对输入图片逐张输出，数组项为：
{message_id, type, finding, comparison, confidence, requires_review, limitation}。
type 只能是 product_label、package_damage、skin_condition、other；message_id 必须取自 context.multimodal_images。
finding 只描述图片中清晰可见的信息；comparison 用于说明与订单或工单的一致/冲突，无可比信息时为空字符串。
只有图片清晰显示与业务记录冲突、严重破损或皮肤异常时 requires_review 才为 true。
皮肤图片不得诊断疾病或判断病因，limitation 必须说明仅供客服登记、需人工确认。
""".strip()
        payload = {
            "context": context,
            "rules_analysis": deterministic,
            "intent_catalog": intent_catalog(),
        }
        return self._request_json(system, payload, image_urls=image_urls)

    def inspect_images(
        self,
        context: dict[str, Any],
        image_urls: list[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        system = """
你是美妆电商客服的图片核对助手。必须逐张查看输入图片，并且只输出 JSON，不要 Markdown。
输出格式：{"visual_observations": [{"message_id":"", "type":"", "finding":"", "comparison":"", "confidence":0.0, "requires_review":false, "limitation":""}]}。
message_id 按 images 数组中的 content_order 与实际图片顺序对应。
type 只能是 product_label、package_damage、skin_condition、other。
finding 用一句中文描述图片中清晰可见的事实；看不清时明确写“图片信息不足，无法可靠识别”，不得猜测。
comparison 仅比较图片事实与 order_info、ticket_info；无可比数据时输出空字符串。
清晰冲突、明显破损或皮肤异常时 requires_review 为 true，否则为 false。
皮肤图片只能描述可见表现，不得诊断疾病、病因或严重程度，limitation 必须提示人工确认。
即使图片与美妆售后无关，也必须以 other 返回一条观察结果。
""".strip()
        payload = {
            "images": context.get("multimodal_images", []),
            "latest_customer_message": next(
                (
                    item
                    for item in reversed(context.get("chat_history", []))
                    if item.get("role") == "customer"
                ),
                None,
            ),
            "order_info": context.get("order_info"),
            "ticket_info": context.get("ticket_info", []),
        }
        return self._request_json(
            system,
            payload,
            max_tokens=1000,
            image_urls=image_urls,
            model=self.settings.qwen_omni_model,
        )

    def draft(self, context: dict[str, Any], analysis: dict[str, Any], knowledge: list[dict[str, Any]], tone: str) -> tuple[dict[str, Any], dict[str, Any]]:
        system = """
你是美妆电商人工客服的建议回复助手。只生成可供人工编辑的草稿，不执行任何操作。
先回应具体感受，再复述当前问题，最后说明下一步。不要使用浮夸称呼和空泛道歉。
已提供的订单、图片或症状不再重复询问。任何时效、退款、赔偿和发货承诺都必须有证据。
不良反应只能记录用户自述、建议停用、交专人回访，情况加重时建议就医，不做诊断和恢复时间预测。
不得使用“马上安排”“立即发出”“全程不耽误”“确保”等无证据承诺；存在冲突时只能说先核对、确认后同步。
只输出 JSON，字段为 reply_draft, tags, used_evidence, commitments。tags 最多 3 条。
commitments 每条为 {content, deadline, evidence_id}；无可支撑承诺时输出空数组。
""".strip()
        payload = {"tone": tone, "context": context, "analysis": analysis, "knowledge": knowledge}
        return self._request_json(system, payload, max_tokens=1300)

    def quality_check(self, context: dict[str, Any], analysis: dict[str, Any], draft: str, rule_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        system = """
你是独立的客服回复质检员，不参与草稿生成。
检查最终待发送文本是否重复索要已有信息、引用错误实体、做无依据承诺、忽略冲突或不良反应。
规则检查已报告的高风险问题不得删除。只输出 JSON：
{ "passed": true, "issues": [{"code":"", "severity":"high|medium", "message":""}], "suggested_rewrite":"" }
""".strip()
        payload = {"context": context, "analysis": analysis, "draft": draft, "rule_result": rule_result}
        return self._request_json(system, payload, max_tokens=900)
