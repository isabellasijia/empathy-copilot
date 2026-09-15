from __future__ import annotations

from datetime import datetime
from typing import Any


SKILL_CATALOG = (
    {"id": "context_builder", "label": "上下文整理", "mode": "local"},
    {"id": "intent_router", "label": "意图路由", "mode": "hybrid"},
    {"id": "order_lookup", "label": "订单查询", "mode": "local"},
    {"id": "ticket_lookup", "label": "工单查询", "mode": "local"},
    {"id": "service_memory", "label": "服务记忆", "mode": "local"},
    {"id": "risk_guard", "label": "风险核对", "mode": "local"},
    {"id": "image_inspection", "label": "图片核对", "mode": "qwen-omni"},
    {"id": "hybrid_knowledge", "label": "混排知识检索", "mode": "local"},
)


def _latest_customer_text(bundle: dict[str, Any]) -> str:
    return next(
        (
            item.get("text") or ""
            for item in reversed(bundle.get("messages", []))
            if item.get("role") == "customer"
        ),
        "",
    )


def is_simple_turn(bundle: dict[str, Any]) -> bool:
    latest = _latest_customer_text(bundle).strip()
    if not latest or len(latest) > 16:
        return False
    simple_phrases = (
        "你好",
        "您好",
        "谢谢",
        "感谢",
        "好的",
        "好哒",
        "收到",
        "知道了",
        "再见",
        "很满意",
        "挺满意",
    )
    return any(phrase in latest for phrase in simple_phrases)


def should_use_understanding_model(
    bundle: dict[str, Any], analysis: dict[str, Any]
) -> bool:
    has_viewable_image = any(
        item.get("content_type") == "image" and item.get("image_available")
        for item in bundle.get("messages", [])
    )
    intent = analysis.get("primary_intent") or {}
    intent_uncertain = bool(
        intent.get("requires_clarification")
        or intent.get("source") in {"out_of_scope", "latest_turn_ambiguous"}
        or float(intent.get("confidence") or 0) < 0.7
    )
    if is_simple_turn(bundle) and not has_viewable_image:
        return False
    return bool(
        has_viewable_image
        or analysis.get("risk_level") in {"high", "medium"}
        or intent_uncertain
    )


def should_retrieve_knowledge(bundle: dict[str, Any]) -> bool:
    return not is_simple_turn(bundle)


def build_service_features(
    bundle: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    messages = bundle.get("messages", [])
    latest = messages[-1] if messages else {}
    return {
        "message_count": len(messages),
        "latest_role": latest.get("role"),
        "latest_content_type": latest.get("content_type") or "text",
        "has_order": bool(bundle.get("order")),
        "ticket_count": len(bundle.get("tickets", [])),
        "image_count": sum(item.get("content_type") == "image" for item in messages),
        "intent_level_1": analysis.get("primary_intent", {}).get("category"),
        "intent_level_2": analysis.get("primary_intent", {}).get("value"),
        "intent_confidence": analysis.get("primary_intent", {}).get("confidence"),
        "intent_source": analysis.get("primary_intent", {}).get("source"),
        "intent_needs_clarification": analysis.get("primary_intent", {}).get("requires_clarification", False),
        "intent_slot_count": len(analysis.get("primary_intent", {}).get("slots") or []),
        "secondary_intent_count": len(analysis.get("secondary_intents", [])),
        "emotion": analysis.get("emotion_state", {}).get("value"),
        "risk_level": analysis.get("risk_level"),
        "simple_turn": is_simple_turn(bundle),
    }


def build_service_graph(bundle: dict[str, Any]) -> dict[str, Any]:
    conversation = bundle.get("conversation", {})
    session_id = conversation.get("session_id")
    customer_id = f"customer:{conversation.get('buyer_nickname') or 'unknown'}"
    nodes = [
        {"id": customer_id, "type": "customer"},
        {"id": f"session:{session_id}", "type": "conversation"},
    ]
    edges = [
        {"from": customer_id, "to": f"session:{session_id}", "relation": "started"}
    ]
    order = bundle.get("order")
    if order:
        order_id = f"order:{order['order_id']}"
        nodes.append({"id": order_id, "type": "order", "status": order.get("status")})
        edges.append({"from": customer_id, "to": order_id, "relation": "placed"})
        if order.get("sku"):
            sku_id = f"sku:{order['sku']}"
            nodes.append({"id": sku_id, "type": "sku", "name": order.get("product_name")})
            edges.append({"from": order_id, "to": sku_id, "relation": "contains"})
    for ticket in bundle.get("tickets", []):
        ticket_id = f"ticket:{ticket['ticket_id']}"
        nodes.append(
            {"id": ticket_id, "type": "ticket", "status": ticket.get("status")}
        )
        edges.append(
            {"from": f"session:{session_id}", "to": ticket_id, "relation": "created"}
        )
        if order:
            edges.append(
                {"from": ticket_id, "to": f"order:{order['order_id']}", "relation": "handles"}
            )
    return {"nodes": nodes, "edges": edges}


def build_skill_trace(
    bundle: dict[str, Any],
    analysis: dict[str, Any],
    *,
    task: str,
    model_called: bool,
    retrieval_count: int = 0,
    cached: bool = False,
) -> dict[str, Any]:
    simple_turn = is_simple_turn(bundle)
    has_images = any(
        item.get("content_type") == "image" and item.get("image_available")
        for item in bundle.get("messages", [])
    )
    has_risk = analysis.get("risk_level") in {"high", "medium"}
    decisions = {
        "context_builder": (True, "整合最新对话与服务状态"),
        "intent_router": (
            True,
            "本地候选已确认"
            if not analysis.get("primary_intent", {}).get("requires_clarification")
            else "候选接近，交深度模型复核",
        ),
        "order_lookup": (
            bool(bundle.get("order")),
            "已关联订单" if bundle.get("order") else "本轮无关联订单",
        ),
        "ticket_lookup": (
            bool(bundle.get("tickets")),
            "已关联售后工单" if bundle.get("tickets") else "本轮无关联工单",
        ),
        "service_memory": (True, "读取有证据的服务记忆"),
        "risk_guard": (
            has_risk or task == "quality",
            "存在风险或进入发送检查"
            if has_risk or task == "quality"
            else "当前无风险信号",
        ),
        "image_inspection": (
            has_images and task == "analysis",
            "检测到可读取图片" if has_images else "本轮没有可读取图片",
        ),
        "hybrid_knowledge": (
            task == "draft" and not simple_turn,
            f"召回 {retrieval_count} 条审核知识"
            if retrieval_count
            else "未命中可用知识",
        ),
    }
    skills = []
    for definition in SKILL_CATALOG:
        called, reason = decisions[definition["id"]]
        skills.append(
            {**definition, "status": "called" if called else "skipped", "reason": reason}
        )

    called_count = sum(item["status"] == "called" for item in skills)
    return {
        "task": task,
        "strategy": "rules_first_on_demand",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "cache_hit": cached,
        "model_called": model_called,
        "called_skills": called_count,
        "skipped_skills": len(skills) - called_count,
        "features": build_service_features(bundle, analysis),
        "skills": skills,
        "agents": [
            {"id": "supervisor", "label": "调度 Agent", "status": "called"},
            {
                "id": "understanding",
                "label": "会话理解 Agent",
                "status": "called" if task == "analysis" else "reused",
            },
            {
                "id": "response",
                "label": "回复生成 Agent",
                "status": "called" if task == "draft" and model_called else "skipped",
            },
            {
                "id": "guard",
                "label": "独立质检 Agent",
                "status": "called" if task == "quality" else "deferred",
            },
        ],
    }
