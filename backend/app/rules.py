from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from .intents import (
    contains_unnegated_phrase,
    context_dependent_turn,
    extract_intent_slots,
    rank_turn_intents,
)


SHADE_PATTERN = re.compile(r"#?\s*(\d{2})\s*([\u4e00-\u9fff]{0,6})")
NEGATIVE_WORDS = ("开啥玩笑", "太离谱", "生气", "不满意", "不开心", "投诉", "差评", "没法用", "走点心", "反复")
ANGER_WORDS = ("我很生气", "非常生气", "火大", "气死", "忍不了", "不能接受")
POSITIVE_WORDS = ("很满意", "满意", "很开心", "谢谢", "感谢", "解决了", "处理得很好", "挺好的")
WORRY_WORDS = ("担心", "害怕", "泛红", "疹子", "痒", "刺痛", "不适")
URGENT_WORDS = ("急", "尽快", "赶紧", "多久", "还没")
SKIN_PROFILE_TERMS = (
    "混敏皮",
    "敏感肌",
    "混油皮",
    "混干皮",
    "油皮",
    "干皮",
    "干性肌肤",
    "暖黄皮",
    "冷白皮",
    "唇纹深",
)

RESOLVED_PHRASES = (
    "问题解决了",
    "已经解决了",
    "已经解决",
    "处理好了",
    "没问题了",
    "没有问题了",
    "确认没问题",
    "收到正确商品",
    "不用处理了",
    "可以结束了",
    "都好了",
)
UNRESOLVED_PHRASES = (
    "还没解决",
    "没有解决",
    "没解决",
    "根本没好",
    "还是有问题",
    "问题还在",
    "还没好",
    "仍然没好",
)
HUMAN_HANDOFF_PHRASES = (
    "转人工",
    "人工客服",
    "真人客服",
    "找人工",
    "找真人",
    "找主管",
    "找经理",
)


def _customer_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in messages if item.get("role") == "customer"]


def _latest_customer_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    customers = _customer_messages(messages)
    return customers[-1] if customers else {}


def customer_confirmed_resolution(messages: list[dict[str, Any]]) -> bool:
    latest = (_latest_customer_message(messages).get("text") or "").strip()
    if (
        not latest
        or contains_unnegated_phrase(latest, UNRESOLVED_PHRASES)
        or any(marker in latest for marker in ("没问题了吗", "解决了吗", "真的解决", "是不是解决"))
    ):
        return False
    return contains_unnegated_phrase(latest, RESOLVED_PHRASES)


def infer_current_intent(bundle: dict[str, Any]) -> dict[str, Any]:
    """Rank the latest turn, then use recent context only for elliptical replies."""
    conversation = bundle["conversation"]
    customers = _customer_messages(bundle.get("messages", []))
    latest = customers[-1] if customers else {}
    latest_text = (latest.get("text") or "").strip()
    evidence = [str(latest["message_id"])] if latest.get("message_id") else []
    latest_slots = extract_intent_slots(latest_text)

    lifecycle_rules = (
        ("需要人工帮助", "转人工", HUMAN_HANDOFF_PHRASES),
        ("反馈问题仍未解决", "服务升级", UNRESOLVED_PHRASES),
        ("确认问题已解决", "服务确认", RESOLVED_PHRASES),
    )
    for value, category, phrases in lifecycle_rules:
        matched = contains_unnegated_phrase(latest_text, phrases)
        if category == "服务确认":
            matched = customer_confirmed_resolution(bundle.get("messages", []))
        if matched:
            return {
                "value": value,
                "category": category,
                "confidence": 0.99,
                "evidence": evidence,
                "source": "latest_turn",
                "requires_clarification": False,
                "candidates": [],
                "slots": latest_slots,
            }

    candidates = rank_turn_intents(latest_text)
    if _shade(latest_text) and any(
        phrase in latest_text for phrase in ("适合", "显", "要", "来一支", "买", "选")
    ) and not any(item["value"] == "色号选择" for item in candidates):
        candidates.insert(0, {"value": "色号选择", "category": "产品咨询", "score": 1.55, "matched_phrases": ["色号实体"]})

    if candidates:
        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        margin = top["score"] - runner_up["score"] if runner_up else top["score"]
        ambiguous = bool(runner_up and margin < 0.32 and runner_up["category"] != top["category"])
        confidence = min(0.98, 0.76 + top["score"] * 0.1 + max(margin, 0) * 0.08)
        return {
            "value": top["value"],
            "category": top["category"],
            "confidence": round(0.62 if ambiguous else confidence, 2),
            "evidence": evidence,
            "source": "latest_turn_ambiguous" if ambiguous else "latest_turn",
            "requires_clarification": ambiguous,
            "candidates": candidates[:3],
            "slots": latest_slots,
        }

    if context_dependent_turn(latest_text):
        for message in list(reversed(customers[:-1]))[:3]:
            ranked = rank_turn_intents(message.get("text") or "")
            if ranked:
                top = ranked[0]
                return {
                    "value": top["value"],
                    "category": top["category"],
                    "confidence": 0.78,
                    "evidence": [str(message["message_id"]), *evidence],
                    "source": "recent_context",
                    "requires_clarification": False,
                    "candidates": ranked[:3],
                    "slots": [
                        *extract_intent_slots(message.get("text") or ""),
                        *latest_slots,
                    ],
                }

    return {
        "value": "需要进一步确认",
        "category": "待确认",
        "confidence": 0.35,
        "evidence": evidence,
        "source": "out_of_scope",
        "requires_clarification": True,
        "candidates": [],
        "slots": latest_slots,
    }


def _shade(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    match = SHADE_PATTERN.search(value)
    if not match:
        return None
    return match.group(1), match.group(2)


def _evidence(source_id: str, source_type: str, label: str, value: str, source_row: int | None = None) -> dict[str, Any]:
    return {
        "id": source_id,
        "source_type": source_type,
        "label": label,
        "value": value,
        "source_row": source_row,
    }


RESHIP_VERBS = ("发出", "寄出", "补发", "重发", "换发", "换新", "为您安排", "已安排", "已下发", "已发出", "已寄出", "重新发", "已换")
RESHIP_RESOLVED_HINTS = ("已核对", "已确认", "已改单", "已改开", "重新开单", "改开", "改单")


def _agent_resolved_shade(messages: list[dict[str, Any]], expected_shade: tuple[str, str] | None) -> dict[str, Any] | None:
    """Return the latest agent message that promised to reship the shade the customer asked for."""
    if not expected_shade:
        return None
    target_code = expected_shade[0]
    for message in reversed(messages):
        if message.get("role") == "customer":
            continue
        text = message.get("text") or ""
        if target_code not in text:
            continue
        if any(word in text for word in RESHIP_VERBS) or any(word in text for word in RESHIP_RESOLVED_HINTS):
            return message
    return None


def _customer_accepted_reship(messages: list[dict[str, Any]], resolved_message: dict[str, Any]) -> bool:
    """Check whether the customer replied positively after the agent promised the correct reship."""
    try:
        resolved_seq = resolved_message.get("message_seq")
    except AttributeError:
        return False
    if resolved_seq is None:
        return False
    for message in messages:
        if message.get("role") != "customer":
            continue
        if (message.get("message_seq") or 0) <= resolved_seq:
            continue
        text = message.get("text") or ""
        if any(word in text for word in ("行", "好的", "好", "可以", "谢谢", "麻烦了", "OK", "ok", "收到")):
            return True
    return False


def detect_conflicts(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    order = bundle.get("order")
    tickets = bundle.get("tickets", [])
    conversation = bundle["conversation"]
    conflicts: list[dict[str, Any]] = []
    if not order:
        return conflicts
    if customer_confirmed_resolution(bundle.get("messages", [])):
        return conflicts

    for ticket in tickets:
        if ticket.get("ticket_kind") != "补发换货":
            continue
        expected_sku = order.get("sku")
        outgoing_sku = ticket.get("product_sku")
        expected_shade = _shade(order.get("product_name"))
        outgoing_shade = _shade(ticket.get("product_name"))
        mismatch = bool(expected_sku and outgoing_sku and expected_sku != outgoing_sku)
        mismatch = mismatch or bool(expected_shade and outgoing_shade and expected_shade[0] != outgoing_shade[0])
        if not mismatch or "错发" not in (conversation.get("scene_minor") or ""):
            continue

        customer_message = next(
            (
                message
                for message in reversed(bundle["messages"])
                if message["role"] == "customer"
                and (
                    (expected_shade and expected_shade[0] in message["text"])
                    or "就要" in message["text"]
                )
            ),
            None,
        )
        evidence = [
            _evidence(
                order["order_id"],
                "订单",
                "原订单商品",
                order.get("product_name") or order.get("sku") or "",
                order.get("source_row"),
            ),
            _evidence(
                ticket["ticket_id"],
                ticket.get("source_sheet") or "工单",
                "工单发出商品",
                ticket.get("product_name") or ticket.get("product_sku") or "",
                ticket.get("source_row"),
            ),
        ]
        if customer_message:
            evidence.insert(
                1,
                _evidence(
                    customer_message["message_id"],
                    "聊天",
                    "用户明确要求",
                    customer_message["text"],
                    customer_message.get("source_row"),
                ),
            )
        conflicts.append(
            {
                "risk_key": f"product_mismatch:{ticket['ticket_id']}",
                "type": "product_mismatch",
                "severity": "high",
                "title": "补发商品与用户诉求不一致",
                "detail": f"用户要求「{order.get('product_name')}」，工单却登记「{ticket.get('product_name')}」。",
                "evidence": evidence,
            }
        )
    return conflicts


def infer_emotion(messages: list[dict[str, Any]]) -> dict[str, Any]:
    customer_messages = [message for message in messages if message["role"] == "customer"]
    recent = " ".join(message["text"] for message in customer_messages[-4:])
    evidence_ids = [message["message_id"] for message in customer_messages[-2:]]
    latest = customer_messages[-1]["text"] if customer_messages else ""
    if any(word in latest for word in ANGER_WORDS):
        return {
            "value": "愤怒",
            "trend": "情绪明显升级，先回应感受",
            "confidence": 0.97,
            "evidence": evidence_ids[-1:],
        }
    if any(word in latest for word in NEGATIVE_WORDS):
        return {
            "value": "不满",
            "trend": "需先回应当前感受",
            "confidence": 0.92,
            "evidence": evidence_ids[-1:],
        }
    if customer_confirmed_resolution(messages) or any(word in latest for word in POSITIVE_WORDS):
        return {
            "value": "满意",
            "trend": "情绪已缓和",
            "confidence": 0.95,
            "evidence": evidence_ids[-1:],
        }
    if any(word in recent for word in NEGATIVE_WORDS):
        return {"value": "不满", "trend": "需先处理信任问题", "confidence": 0.88, "evidence": evidence_ids}
    if any(word in recent for word in WORRY_WORDS):
        return {"value": "担心", "trend": "需明确安全边界和回访", "confidence": 0.9, "evidence": evidence_ids}
    if any(word in recent for word in URGENT_WORDS):
        return {"value": "着急", "trend": "关注处理时效", "confidence": 0.82, "evidence": evidence_ids}
    return {"value": "平稳", "trend": "无明显升级", "confidence": 0.68, "evidence": evidence_ids}


def _service_stage(tickets: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> str:
    if conflicts:
        return "方案沟通"
    if not tickets:
        return "信息确认"
    statuses = " ".join(ticket.get("status") or "" for ticket in tickets)
    if any(value in statuses for value in ("待处理", "待审核", "等待")):
        return "等待处理"
    if any(value in statuses for value in ("进行中", "处理中", "仓库处理", "工单组处理")):
        return "工单执行中"
    if all("完结" in (ticket.get("status") or "") or "成功" in (ticket.get("status") or "") for ticket in tickets):
        return "结果反馈"
    return "方案沟通"


def infer_resolution(
    bundle: dict[str, Any],
    risks: list[dict[str, Any]],
    emotion: dict[str, Any],
) -> dict[str, Any]:
    """Estimate case resolution from observable service evidence, not sentiment alone."""
    messages = bundle.get("messages", [])
    tickets = bundle.get("tickets", [])
    customer_messages = [item for item in messages if item.get("role") == "customer"]
    agent_messages = [item for item in messages if item.get("role") != "customer"]
    latest_customer = customer_messages[-1] if customer_messages else {}
    latest_agent = agent_messages[-1] if agent_messages else {}
    latest_customer_text = latest_customer.get("text") or ""
    latest_agent_text = latest_agent.get("text") or ""
    ticket_statuses = " ".join(item.get("status") or "" for item in tickets)
    evidence: list[str] = []

    score = 25
    if tickets:
        score += 15
        evidence.extend(str(item["ticket_id"]) for item in tickets[-2:])
    if any(value in ticket_statuses for value in ("进行中", "处理中", "仓库处理", "工单组处理")):
        score += 20
    elif any(value in ticket_statuses for value in ("待处理", "待审核", "等待")):
        score += 10
    if tickets and all(
        any(value in (item.get("status") or "") for value in ("完结", "成功", "已关闭"))
        for item in tickets
    ):
        score += 30

    if any(value in latest_agent_text for value in ("已核对", "正在核对", "已提交", "跟进", "处理")):
        score += 8
        if latest_agent.get("message_id"):
            evidence.append(str(latest_agent["message_id"]))

    explicit_resolved = customer_confirmed_resolution(messages)
    if explicit_resolved:
        score += 25
    elif emotion.get("value") == "满意":
        score += 10
    if latest_customer.get("message_id"):
        evidence.append(str(latest_customer["message_id"]))

    high_risks = [item for item in risks if item.get("severity") == "high"]
    if emotion.get("value") in ("愤怒", "不满"):
        score = min(score, 28)
        stage = "先安抚"
        summary = "客户仍有明显不满，先回应感受并明确下一步。"
    elif explicit_resolved and not any(
        item.get("type") == "adverse_reaction" for item in high_risks
    ):
        score = 100
        stage = "已解决"
        summary = "客户已明确确认问题解决，可以完成记录并结束本次服务。"
    elif high_risks:
        score = min(score, 68)
        stage = "待核对"
        if emotion.get("value") == "满意":
            summary = "客户情绪已缓和，但关键问题仍需核对后才能关闭。"
        else:
            summary = "关键问题尚未排除，核对完成前不要确认已解决。"
    elif any(value in ticket_statuses for value in ("完结", "成功", "已关闭")):
        score = max(score, 84)
        stage = "待确认"
        summary = "处理已完成，等待客户确认最终结果。"
    elif tickets:
        stage = "处理中"
        summary = "处理已进入工单流程，继续同步进展并等待结果。"
    else:
        stage = "刚开始"
        summary = "已识别客户诉求，下一步需要确认处理方案。"

    return {
        "score": max(0, min(round(score), 100)),
        "stage": stage,
        "summary": summary,
        "evidence": list(dict.fromkeys(evidence)),
    }


def build_memory(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    order = bundle.get("order")
    if order:
        facts.append(
            {
                "kind": "service_fact",
                "label": "订单商品",
                "value": order.get("product_name") or order.get("sku"),
                "status": "confirmed",
                "source_id": order["order_id"],
                "source_type": "订单",
                "event_time": order.get("ordered_at"),
            }
        )
        if order.get("tracking_no"):
            facts.append(
                {
                    "kind": "service_fact",
                    "label": "原包裹",
                    "value": f"{order.get('carrier') or ''} {order['tracking_no']}".strip(),
                    "status": "confirmed",
                    "source_id": order["order_id"],
                    "source_type": "订单",
                    "event_time": order.get("shipped_at"),
                }
            )

    for message in bundle["messages"]:
        if message["role"] == "customer" and message["content_type"] == "image":
            facts.append(
                {
                    "kind": "service_event",
                    "label": "图片凭证已提供",
                    "value": message["text"] or "用户已上传图片",
                    "status": "confirmed",
                    "source_id": message["message_id"],
                    "source_type": "聊天",
                    "event_time": message.get("sent_at"),
                    "image_available": bool(message.get("image_available")),
                }
            )

    for ticket in bundle.get("tickets", []):
        detail_parts = [ticket.get("category"), ticket.get("reason"), ticket.get("status")]
        facts.append(
            {
                "kind": "service_event",
                "label": f"{ticket.get('ticket_kind') or ''}工单",
                "value": " / ".join(value for value in detail_parts if value),
                "status": "disputed" if "冲突" in (ticket.get("status") or "") else "confirmed",
                "source_id": ticket["ticket_id"],
                "source_type": ticket.get("source_sheet") or "工单",
                "event_time": ticket.get("created_at"),
            }
        )

    for commitment in bundle.get("commitments", []):
        facts.append(
            {
                "kind": "commitment",
                "label": "服务承诺",
                "value": commitment["content"],
                "status": commitment["status"],
                "source_id": commitment.get("source_message_id") or commitment.get("evidence_id"),
                "source_type": "聊天",
                "event_time": commitment.get("created_at"),
                "deadline": commitment.get("deadline"),
            }
        )
    return facts


def build_customer_profile(bundle: dict[str, Any]) -> dict[str, Any]:
    customer_messages = [
        message for message in bundle["messages"] if message["role"] == "customer"
    ]
    combined = " ".join(message["text"] for message in customer_messages)
    traits: list[dict[str, Any]] = []

    for term in SKIN_PROFILE_TERMS:
        if term not in combined:
            continue
        evidence = next(
            message["message_id"]
            for message in reversed(customer_messages)
            if term in message["text"]
        )
        traits.append(
            {
                "label": "肤质关注" if "皮" in term or "肌肤" in term else "使用特征",
                "value": term,
                "evidence": [evidence],
            }
        )
        if len(traits) >= 2:
            break

    shade_messages = [
        message
        for message in customer_messages
        if _shade(message["text"])
        and any(word in message["text"] for word in ("要", "适合", "买", "只接受", "就要"))
    ]
    if shade_messages:
        shade_message = shade_messages[-1]
        shade = _shade(shade_message["text"])
        if shade:
            shade_value = f"#{shade[0]}{shade[1]}".rstrip()
            traits.append(
                {
                    "label": "色号偏好",
                    "value": shade_value,
                    "evidence": [shade_message["message_id"]],
                }
            )

    recent_text = " ".join(message["text"] for message in customer_messages[-4:])
    if any(word in recent_text for word in URGENT_WORDS):
        evidence = next(
            message["message_id"]
            for message in reversed(customer_messages)
            if any(word in message["text"] for word in URGENT_WORDS)
        )
        traits.append(
            {
                "label": "沟通关注",
                "value": "希望及时同步进度",
                "evidence": [evidence],
            }
        )
    if any(word in recent_text for word in NEGATIVE_WORDS):
        evidence = next(
            message["message_id"]
            for message in reversed(customer_messages)
            if any(word in message["text"] for word in NEGATIVE_WORDS)
        )
        traits.append(
            {
                "label": "服务关注",
                "value": "需要明确回应处理差错",
                "evidence": [evidence],
            }
        )

    unique_traits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for trait in traits:
        key = (trait["label"], trait["value"])
        if key in seen:
            continue
        seen.add(key)
        unique_traits.append(trait)

    if unique_traits:
        summary = "；".join(item["value"] for item in unique_traits[:3])
        summary = f"本次服务可参考：{summary}。"
    else:
        summary = "暂未发现需要长期保留的服务偏好，本轮以当前诉求为准。"
    return {
        "summary": summary,
        "traits": unique_traits[:4],
        "scope": "仅整理用户主动提供、与本次服务相关的信息",
    }


def deterministic_analysis(bundle: dict[str, Any]) -> dict[str, Any]:
    conversation = bundle["conversation"]
    messages = bundle["messages"]
    conflicts = detect_conflicts(bundle)
    emotion = infer_emotion(messages)
    current_intent = infer_current_intent(bundle)
    major = conversation.get("scene_major") or "其他服务"
    minor = current_intent["value"]
    customer_ids = [message["message_id"] for message in messages if message["role"] == "customer"]
    primary_evidence = customer_ids[-2:] or [message["message_id"] for message in messages[-2:]]

    # 错发场景下，是否客服已经在聊天里改口重发正确色号
    order = bundle.get("order") or {}
    expected_shade = _shade(order.get("product_name"))
    reship_resolved_message = None
    if "错发" in (conversation.get("scene_minor") or "") and expected_shade:
        reship_resolved_message = _agent_resolved_shade(messages, expected_shade)

    # 客户最新一两条消息是否在追问物流 / 发货时间
    latest_customer_texts = [message.get("text") or "" for message in messages if message.get("role") == "customer"][-2:]
    logistics_keywords = ("发货时间", "多久", "几天", "什么时候", "何时", "物流", "快递", "送到", "到货", "什么时候能")
    customer_asking_logistics = any(any(word in text for word in logistics_keywords) for text in latest_customer_texts)

    secondary: list[dict[str, Any]] = []
    if conflicts:
        secondary.append({"value": "避免再次处理错误", "confidence": 1.0, "evidence": [item["id"] for item in conflicts[0]["evidence"]]})
    if emotion["value"] in ("不满", "愤怒"):
        secondary.append({"value": "恢复对服务的信任", "confidence": 0.82, "evidence": emotion["evidence"]})
    if major == "不良反应" or current_intent["category"] == "不良反应":
        secondary.append({"value": "获得安全处理与回访", "confidence": 0.96, "evidence": primary_evidence})

    risks = list(conflicts)
    if major == "不良反应" or current_intent["category"] == "不良反应":
        ticket = next((item for item in bundle.get("tickets", []) if item.get("ticket_kind") == "不良反应"), None)
        evidence = [_evidence(message["message_id"], "聊天", "用户自述", message["text"], message.get("source_row")) for message in messages if message["role"] == "customer" and any(word in message["text"] for word in WORRY_WORDS)][:3]
        if ticket:
            evidence.append(_evidence(ticket["ticket_id"], ticket["source_sheet"], "不良反应工单", ticket.get("reason") or ticket.get("status") or "", ticket.get("source_row")))
        risks.append(
            {
                "risk_key": f"adverse_reaction:{conversation['session_id']}",
                "type": "adverse_reaction",
                "severity": "high",
                "title": "不良反应需专人跟进",
                "detail": "用户描述了皮肤不适，应避免诊断性表述，并转交专业人员持续回访。",
                "evidence": evidence,
            }
        )

    if current_intent["category"] == "服务确认":
        summary = "用户已明确表示问题解决，可以完成记录并结束本次服务。"
        next_actions = [
            {"title": "确认结束本次服务", "detail": "记录用户确认，无需继续重复跟进。", "priority": "normal"},
        ]
    elif current_intent["category"] == "转人工":
        summary = "用户明确希望由人工处理，请立即接管并保留前序信息。"
        next_actions = [
            {"title": "人工立即接管", "detail": "不用让用户重复说明，直接承接当前问题。", "priority": "high"},
        ]
    elif current_intent.get("requires_clarification"):
        candidates = current_intent.get("candidates") or []
        candidate_names = "、".join(item["value"] for item in candidates[:2])
        summary = (
            f"这句话可能同时涉及「{candidate_names}」，先确认用户这次最想处理哪一件事。"
            if candidate_names
            else "暂时无法确定用户这次想处理什么，先用一句话确认，不要沿用旧问题猜测。"
        )
        next_actions = [
            {"title": "先确认当前问题", "detail": "请用户说明这次最想先处理的一件事。", "priority": "normal"},
        ]
    elif conflicts and current_intent["category"] == "物流服务":
        summary = f"用户当前在问「{minor}」，但补发商品记录仍有冲突，核对正确色号后再同步物流。"
        next_actions = [
            {"title": "先核对补发商品", "detail": "确认色号无误后再查询真实物流节点。", "priority": "high"},
            {"title": "同步真实物流", "detail": "只引用订单页已有运单和状态。", "priority": "normal"},
        ]
    elif conflicts:
        summary = conflicts[0]["detail"] + " 先核对清楚，再回复用户。"
        next_actions = [
            {"title": "先别确认已经补发", "detail": "色号没核对清楚前，不要承诺发货。", "priority": "high"},
            {"title": "核对补发色号", "detail": "对照订单、用户消息和补发单。", "priority": "high"},
            {"title": "核对后回复用户", "detail": "如果已经发错，请交给主管处理。", "priority": "normal"},
        ]
    elif reship_resolved_message and customer_asking_logistics:
        shade_label = f"#{expected_shade[0]}{expected_shade[1]}".rstrip() if expected_shade else "正确色号"
        summary = f"补发商品已改开为「{shade_label}」并向用户确认。用户现在想知道发货时间，物流信息以订单页为准，请不要口头承诺时效。"
        next_actions = [
            {"title": "查真实运单状态", "detail": f"打开{shade_label} 补发单，核对是否已出库、是否已生成运单号；以系统里的真实节点为准。", "priority": "high"},
            {"title": "引导用户看订单页", "detail": "把「我的订单-物流详情」的入口发给用户，让物流状态由系统实时显示，避免口头承诺时效。", "priority": "high"},
            {"title": "无运单时升级跟进", "detail": "如果系统里还没有运单号，登记跟进人并承诺主动回追时间，不要甩“尽快/几天”这种模糊话术。", "priority": "normal"},
        ]
    elif reship_resolved_message:
        shade_label = f"#{expected_shade[0]}{expected_shade[1]}".rstrip() if expected_shade else "正确色号"
        summary = f"补发商品已改开为「{shade_label}」，处于跟进补发进度阶段，请让用户在订单页看到最新物流。"
        next_actions = [
            {"title": "跟进补发进度", "detail": f"确认{shade_label} 补发单当前的仓库/物流节点，运单号一旦生成即同步。", "priority": "high"},
            {"title": "引导用户看订单页", "detail": "让用户在「我的订单-物流详情」实时查看进度，避免口头给时效。", "priority": "normal"},
        ]
    elif major == "不良反应" or current_intent["category"] == "不良反应":
        summary = "用户用了产品后不舒服。不要判断病因，请交给专人跟进。"
        next_actions = [
            {"title": "交给专人处理", "detail": "不要判断原因，也不要让用户继续试用。", "priority": "high"},
            {"title": "确认回访时间", "detail": "情况加重时，提醒用户及时就医。", "priority": "high"},
        ]
    else:
        summary = f"用户当前想处理「{minor}」。请结合已有订单和工单直接承接。"
        next_actions = [
            {"title": "查看当前处理进度", "detail": "使用已关联的订单和工单，避免让用户重复说明。", "priority": "normal"},
            {"title": "只确认关键缺失信息", "detail": "如无关键缺失项，直接给出可执行的下一步。", "priority": "normal"},
        ]

    risk_level = "high" if any(item["severity"] == "high" for item in risks) else "medium" if risks else "none"
    resolution = infer_resolution(bundle, risks, emotion)
    route_reasons: list[str] = []
    if current_intent["category"] == "转人工":
        route_reasons.append("用户主动要求人工")
    if risk_level == "high":
        route_reasons.append("存在高风险事项")
    if emotion["value"] in ("愤怒", "不满"):
        route_reasons.append("客户情绪需要人工承接")
    service_route = {
        "mode": "human" if route_reasons else "ai",
        "label": "转人工处理" if route_reasons else "AI 可继续接待",
        "reason": "；".join(route_reasons) if route_reasons else "当前问题低风险且信息充分",
        "confidence": 0.98 if route_reasons else 0.86,
    }
    return {
        "primary_intent": current_intent,
        "secondary_intents": secondary,
        "service_stage": {"value": "服务完成" if current_intent["category"] == "服务确认" else _service_stage(bundle.get("tickets", []), conflicts), "confidence": 0.9, "evidence": [ticket["ticket_id"] for ticket in bundle.get("tickets", [])]},
        "emotion_state": emotion,
        "resolution_state": resolution,
        "risk_level": risk_level,
        "risk_signals": risks,
        "intent_shift": {
            "from": major,
            "to": secondary[0]["value"] if secondary else minor,
            "trigger": conflicts[0]["title"] if conflicts else "当前最新诉求",
            "evidence": [item["id"] for item in conflicts[0]["evidence"]] if conflicts else primary_evidence,
        },
        "summary": summary,
        "next_actions": next_actions,
        "memory": build_memory(bundle),
        "customer_profile": build_customer_profile(bundle),
        "visual_observations": [],
        "service_route": service_route,
        "source": "rules",
    }


def fallback_reply(bundle: dict[str, Any], analysis: dict[str, Any], tone: str = "自然") -> dict[str, Any]:
    order = bundle.get("order") or {}
    tickets = bundle.get("tickets", [])
    primary = analysis["primary_intent"]["value"]
    conversation = bundle["conversation"]
    messages = bundle["messages"]

    # 是否处于“错发已改口”阶段，用于决定回复走向
    expected_shade = _shade(order.get("product_name"))
    reship_resolved_message = None
    if "错发" in (conversation.get("scene_minor") or "") and expected_shade:
        reship_resolved_message = _agent_resolved_shade(messages, expected_shade)
    latest_customer_texts = [message.get("text") or "" for message in messages if message.get("role") == "customer"][-2:]
    logistics_keywords = ("发货时间", "多久", "几天", "什么时候", "何时", "物流", "快递", "送到", "到货", "什么时候能")
    customer_asking_logistics = any(any(word in text for word in logistics_keywords) for text in latest_customer_texts)

    if analysis["primary_intent"].get("requires_clarification"):
        candidates = analysis["primary_intent"].get("candidates") or []
        names = "、".join(item["value"] for item in candidates[:2])
        reply = (
            f"我先确认一下，您这次主要想先处理「{names}」中的哪一项？确认后我马上按这一件事继续帮您。"
            if names
            else "我想先准确理解您的需求。请问您这次最想先处理的是商品咨询、物流、退款，还是售后问题？"
        )
        tags = ["确认当前问题", "避免错误处理"]
    elif any(item["type"] == "product_mismatch" for item in analysis["risk_signals"]):
        reply = "抱歉让您久等了。我在核对换货记录时发现，补发商品与您确认的色号不一致。为避免再次发错，我已暂停普通处理并优先复核实际补发商品，确认后会立即同步给您。"
        tags = ["已发现冲突", "避免二次错发", "暂不过度承诺"]
    elif analysis["primary_intent"].get("category") == "服务确认":
        reply = "收到，感谢您确认问题已经解决。我已为您记录本次处理结果；后续还有需要，随时联系我们。"
        tags = ["用户已确认", "结束服务"]
    elif analysis["primary_intent"].get("category") == "产品咨询":
        latest_customer = next(
            (item for item in reversed(messages) if item.get("role") == "customer"),
            {},
        )
        latest_text = latest_customer.get("text") or ""
        if primary == "色号选择":
            reply = "收到，您主要在意色号是否适合自己的肤色和日常场景。我会结合您已经说过的肤色、唇部特点和妆效偏好给建议，不会只推荐热门色。"
            tags = ["色号建议", "结合个人偏好"]
        elif any(value in latest_text for value in ("敏感肌", "过敏", "刺激")):
            reply = "理解您会在意敏感肌适配。是否适合需要结合产品完整成分和您的实际肤况判断；首次使用建议先在局部少量试用，如出现不适请立即停用。"
            tags = ["敏感肌关注", "安全使用"]
        elif primary == "辨别商品真伪":
            reply = "理解您对真伪的担心。我会先核对订单渠道、包装批次和可验证的防伪信息；仅凭单张照片不能直接下结论，必要时会转人工复核。"
            tags = ["真伪核对", "避免武断结论"]
        else:
            reply = f"收到，您想了解「{primary}」。我会根据品牌产品说明和您已经提供的信息核对，涉及成分适用性时不会做没有依据的保证。"
            tags = ["产品咨询", "依据产品说明"]
    elif reship_resolved_message and customer_asking_logistics:
        shade_label = f"#{expected_shade[0]}{expected_shade[1]}".rstrip() if expected_shade else "正确色号"
        reply = (
            f"感谢您的耐心。{shade_label} 补发单我已经加急处理，最新的物流状态您可以在「我的订单 - 物流详情」里实时看到。"
            "如果那边一直没有更新，我这边会主动帮您催仓，运单号一有就第一时间发给您。"
        )
        tags = ["引导订单页查物流", "不口头承诺时效", "无更新时主动催"]
    elif analysis["primary_intent"].get("category") == "不良反应":
        reply = "理解您现在会担心。您提供的不适情况已经完整记录，不需要重复说明。这边会交给专业团队跟进；请先停止使用该产品，如果症状加重或范围扩大，请及时就医。"
        tags = ["先关心用户", "不做诊断", "交专人跟进"]
    elif analysis["primary_intent"].get("category") == "物流服务":
        status = order.get("status") or "待查询"
        tracking = " ".join(value for value in (order.get("carrier"), order.get("tracking_no")) if value)
        reply = f"收到，您在查询物流进度。当前订单状态为「{status}」{f'，物流信息为 {tracking}' if tracking else ''}。我会以订单页的真实节点为准，不口头承诺尚未确认的时间。"
        tags = ["物流查询", "引用真实节点"]
    elif analysis["primary_intent"].get("category") == "退款打款":
        ticket = tickets[0] if tickets else {}
        status = ticket.get("status") or "待核对"
        reply = f"收到，您在查询退款进度。当前相关记录状态为「{status}」。我会继续核对退款渠道和到账节点，不会让您重复提交已经提供的信息。"
        tags = ["退款进度", "核对到账节点"]
    elif analysis["primary_intent"].get("category") == "补发换货":
        if primary == "处理商品破损":
            reply = "抱歉商品到手时出现破损。我已经关联当前订单和您提供的信息，会先核对破损凭证与售后记录；已有照片不需要重复上传。"
            tags = ["商品破损", "不重复索要凭证"]
        elif primary == "处理错发漏发":
            reply = "明白，您反馈收到的商品有错发或漏发。我会对照订单商品与实际收到的内容核对，确认前不会直接承诺补发，避免再次处理错误。"
            tags = ["错发漏发", "先核对再处理"]
        else:
            reply = "明白，您这次希望办理换货。我会按换货诉求核对当前订单和售后记录，不会误按退款处理；已有信息无需重复说明。"
            tags = ["换货诉求", "避免错误分流"]
    else:
        ticket = tickets[0] if tickets else {}
        status = ticket.get("status") or order.get("status") or "处理中"
        ticket_text = f"工单 {ticket['ticket_id']} " if ticket.get("ticket_id") else ""
        reply = f"理解您在等待处理结果。我已核对您的订单和服务记录，当前{ticket_text}状态为「{status}」。已经提供的信息不需要重复发送，我会按当前进度继续跟进。"
        tags = ["已核对记录", "不重复追问", "说明下一步"]

    if tone == "简洁":
        sentences = [part for part in re.split(r"(?<=[。！？])", reply) if part.strip()]
        reply = "".join(sentences[:3])
    elif tone == "更关心" and not reply.startswith("真的"):
        reply = "真的很抱歉让您这次购物多费心了。" + reply

    evidence = list(analysis["primary_intent"]["evidence"])
    evidence.extend(ticket["ticket_id"] for ticket in tickets[:2])
    if order.get("order_id"):
        evidence.append(order["order_id"])
    return {
        "reply_draft": reply,
        "tags": tags,
        "used_evidence": list(dict.fromkeys(evidence)),
        "commitments": [],
        "provider": "local",
        "model": None,
    }


def deterministic_quality_check(bundle: dict[str, Any], analysis: dict[str, Any], text: str) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    lowered = text.lower()
    conflicts = [item for item in analysis["risk_signals"] if item["type"] == "product_mismatch"]
    has_image = any(message["content_type"] == "image" for message in bundle["messages"] if message["role"] == "customer")

    if has_image and re.search(r"(再|重新|麻烦).{0,8}(发|提供|上传).{0,8}(图|照片|图片)", text):
        issues.append({"code": "repeat_ask", "severity": "high", "message": "用户已提供图片，不应再次索要。"})

    if conflicts and re.search(r"(已经|现已|确认).{0,10}(无误|发出|补发|完成)", text):
        issues.append({"code": "unresolved_conflict", "severity": "high", "message": "工单存在未解决的商品冲突，不能确认已正常发出。"})

    if conflicts:
        wrong_product = conflicts[0]["evidence"][-1]["value"]
        wrong_shade = _shade(wrong_product)
        if wrong_shade and re.search(rf"(补发|发出|安排).{{0,10}}#?\s*{wrong_shade[0]}", text):
            issues.append({"code": "wrong_product", "severity": "high", "message": f"回复把冲突色号 #{wrong_shade[0]} 当成了将要发出的商品。"})

    if any(
        term in text
        for term in (
            "保证",
            "确保",
            "百分之百",
            "一定到",
            "肯定到",
            "马上到账",
            "马上安排",
            "立即发出",
            "全程不耽误",
        )
    ):
        issues.append({"code": "unsupported_promise", "severity": "high", "message": "回复包含系统不能确保的结果或时效承诺。"})

    if analysis["primary_intent"].get("category") == "不良反应" or any(
        item.get("type") == "adverse_reaction" for item in analysis.get("risk_signals", [])
    ):
        if re.search(r"(皮炎|确诊|治愈|自行缓解|\d+天.{0,4}恢复)", text):
            issues.append({"code": "medical_claim", "severity": "high", "message": "不良反应回复不应做诊断或恢复时间判断。"})
        if not any(term in text for term in ("停用", "停止使用", "专业团队", "就医")):
            issues.append({"code": "missing_safety_action", "severity": "high", "message": "回复未包含必要的安全处理或专人跟进说明。"})

    return {
        "passed": not any(issue["severity"] == "high" for issue in issues),
        "issues": issues,
        "checks": {
            "no_repeat_ask": not any(issue["code"] == "repeat_ask" for issue in issues),
            "no_wrong_reference": not any(issue["code"] in {"wrong_product", "unresolved_conflict"} for issue in issues),
            "no_unsupported_promise": not any(issue["code"] in {"unsupported_promise", "medical_claim"} for issue in issues),
            "no_missing_signal": not any(issue["code"] == "missing_safety_action" for issue in issues),
        },
        "provider": "rules",
    }


def extract_commitments(text: str, now: datetime | None = None) -> list[dict[str, str | None]]:
    now = now or datetime.now()
    commitments = []
    for match in re.finditer(r"(\d{1,3})\s*(小时|天)内", text):
        amount = int(match.group(1))
        delta = timedelta(hours=amount) if match.group(2) == "小时" else timedelta(days=amount)
        commitments.append(
            {
                "content": text,
                "deadline": (now + delta).isoformat(timespec="seconds"),
                "evidence_id": None,
            }
        )
    return commitments
