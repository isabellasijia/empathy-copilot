from __future__ import annotations

import time
from datetime import datetime
from typing import Any

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


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


class WorkflowTrace:
    """Record only workflow nodes that actually ran for the current request."""

    def __init__(
        self,
        task: str,
        bundle: dict[str, Any],
        *,
        started_at: float | None = None,
    ) -> None:
        self.task = task
        self.bundle = bundle
        self.started_at = started_at if started_at is not None else time.perf_counter()
        self.created_at = datetime.now().isoformat(timespec="milliseconds")
        self.steps: list[dict[str, Any]] = []
        self.transitions: list[dict[str, str]] = []
        self.current_state = "received"
        self.model_called = False
        self.cache_hit = False

    def add_step(
        self,
        step_id: str,
        label: str,
        *,
        engine: str,
        reason: str,
        latency_ms: float,
        output_state: str,
        input_summary: str = "",
        output_summary: str = "",
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        step = {
            "id": step_id,
            "label": label,
            "engine": engine,
            "status": status,
            "reason": reason,
            "latency_ms": round(float(latency_ms or 0), 2),
            "input_summary": input_summary,
            "output_summary": output_summary,
        }
        if metadata:
            step["metadata"] = {
                key: value
                for key, value in metadata.items()
                if key in {"model", "input_tokens", "output_tokens", "provider"}
                and value is not None
            }
        self.steps.append(step)
        if engine.startswith("qwen") or (metadata or {}).get("provider") == "qwen":
            self.model_called = True
        if engine == "cache":
            self.cache_hit = True
        if output_state != self.current_state:
            self.transitions.append(
                {
                    "from": self.current_state,
                    "to": output_state,
                    "trigger": step_id,
                }
            )
            self.current_state = output_state

    def finish(
        self,
        analysis: dict[str, Any],
        *,
        final_state: str | None = None,
    ) -> dict[str, Any]:
        route = analysis.get("service_route") or {}
        target_state = final_state or (
            "human_review_required" if route.get("mode") == "human" else "completed"
        )
        if target_state != self.current_state:
            self.transitions.append(
                {"from": self.current_state, "to": target_state, "trigger": "workflow_end"}
            )
            self.current_state = target_state
        return {
            "task": self.task,
            "strategy": "evidence_first_workflow",
            "created_at": self.created_at,
            "completed_at": datetime.now().isoformat(timespec="milliseconds"),
            "total_latency_ms": elapsed_ms(self.started_at),
            "cache_hit": self.cache_hit,
            "model_called": self.model_called,
            "features": build_service_features(self.bundle, analysis),
            "steps": self.steps,
            "state_transitions": self.transitions,
            "final_state": self.current_state,
            "human_handoff": {
                "required": route.get("mode") == "human",
                "reason": route.get("reason") or "",
            },
        }
