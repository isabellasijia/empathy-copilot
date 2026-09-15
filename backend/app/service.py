from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from .ai import QwenService
from .config import Settings
from .db import database
from .etl import import_workbook
from .intents import is_allowed_intent
from .orchestration import (
    SKILL_CATALOG,
    build_service_graph,
    build_skill_trace,
    is_simple_turn,
    should_retrieve_knowledge,
    should_use_understanding_model,
)
from .rag import load_knowledge, search_knowledge
from .rules import (
    customer_confirmed_resolution,
    deterministic_analysis,
    deterministic_quality_check,
    extract_commitments,
    fallback_reply,
)


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _severity_rank(value: str) -> int:
    return {"high": 0, "medium": 1}.get(value, 2)


IMAGE_DATA_URL = re.compile(
    r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\r\n]+)$"
)
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class EmpathyService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ai = QwenService(settings)
        self._analysis_locks: dict[str, Lock] = {}
        self._analysis_locks_guard = Lock()

    def _analysis_lock(self, session_id: str) -> Lock:
        with self._analysis_locks_guard:
            return self._analysis_locks.setdefault(session_id, Lock())

    def initialize(self, force_import: bool = False) -> dict[str, Any]:
        report = import_workbook(
            self.settings.workbook_path,
            self.settings.database_path,
            force=force_import,
        )
        report["knowledge_documents"] = load_knowledge(
            self.settings.knowledge_dir, self.settings.database_path
        )
        self.seed_derived_records()
        return report

    def stats(self) -> dict[str, Any]:
        with database(self.settings.database_path) as connection:
            report_row = connection.execute(
                "SELECT value FROM app_meta WHERE key = 'etl_report'"
            ).fetchone()
            report = json.loads(report_row["value"]) if report_row else {}
            open_risks = connection.execute(
                "SELECT COUNT(*) AS value FROM risk_events WHERE status != '已关闭'"
            ).fetchone()["value"]
            overdue = connection.execute(
                """
                SELECT COUNT(*) AS value FROM commitments
                WHERE status != '已关闭' AND deadline IS NOT NULL AND deadline < ?
                """,
                (datetime.now().isoformat(timespec="seconds"),),
            ).fetchone()["value"]
            unread_messages = connection.execute(
                "SELECT COALESCE(SUM(unread_count), 0) AS value FROM conversation_states"
            ).fetchone()["value"]
        return {
            **report,
            "open_risks": open_risks,
            "overdue_commitments": overdue,
            "unread_messages": unread_messages,
            "ai": {
                "configured": self.ai.configured,
                "mode": "online" if self.ai.configured else "rehearsal",
                "model": self.settings.qwen_text_model,
                "text_model": self.settings.qwen_text_model,
                "omni_model": self.settings.qwen_omni_model,
            },
        }

    def evaluation_summary(self) -> dict[str, Any]:
        def evaluation_bundle(session_id: str) -> dict[str, Any]:
            bundle = self.get_bundle(session_id)
            bundle["messages"] = [
                item
                for item in bundle["messages"]
                if int(item.get("source_row") or 0) > 0
            ]
            return bundle

        conflict_bundle = evaluation_bundle("S00018")
        conflict_analysis = deterministic_analysis(conflict_bundle)
        conflict_detected = any(
            item["type"] == "product_mismatch"
            for item in conflict_analysis["risk_signals"]
        )

        repeat_check = deterministic_quality_check(
            evaluation_bundle("S00001"),
            deterministic_analysis(evaluation_bundle("S00001")),
            "麻烦您再重新发一次破损照片。",
        )
        medical_check = deterministic_quality_check(
            evaluation_bundle("S00082"),
            deterministic_analysis(evaluation_bundle("S00082")),
            "你这是过敏性皮炎，2天一定恢复。",
        )

        anger_bundle = evaluation_bundle("S00018")
        anger_bundle["messages"] = [
            *anger_bundle["messages"],
            {
                "message_id": "EVAL-ANGER",
                "role": "customer",
                "text": "我很生气，这个问题请不要再敷衍我。",
                "content_type": "text",
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            },
        ]
        anger_analysis = deterministic_analysis(anger_bundle)

        satisfaction_bundle = evaluation_bundle("S00018")
        satisfaction_bundle["messages"] = [
            *satisfaction_bundle["messages"],
            {
                "message_id": "EVAL-SATISFIED",
                "role": "customer",
                "text": "我对服务很满意，谢谢。",
                "content_type": "text",
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            },
        ]
        satisfaction_analysis = deterministic_analysis(satisfaction_bundle)
        consultation_analysis = deterministic_analysis(evaluation_bundle("S00019"))
        profile_values = {
            item["value"]
            for item in consultation_analysis["customer_profile"]["traits"]
        }
        logistics_bundle = evaluation_bundle("S00018")
        logistics_bundle["messages"] = [
            *logistics_bundle["messages"],
            {
                "message_id": "EVAL-LATEST-INTENT",
                "role": "customer",
                "text": "补发的快递现在到哪里了？",
                "content_type": "text",
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            },
        ]
        logistics_analysis = deterministic_analysis(logistics_bundle)
        resolved_bundle = evaluation_bundle("S00018")
        resolved_bundle["messages"] = [
            *resolved_bundle["messages"],
            {
                "message_id": "EVAL-RESOLVED",
                "role": "customer",
                "text": "已经收到正确商品了，没问题了，谢谢。",
                "content_type": "text",
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            },
        ]
        resolved_analysis = deterministic_analysis(resolved_bundle)
        handoff_bundle = evaluation_bundle("S00019")
        handoff_bundle["messages"] = [
            *handoff_bundle["messages"],
            {
                "message_id": "EVAL-HANDOFF",
                "role": "customer",
                "text": "我想转人工客服。",
                "content_type": "text",
                "sent_at": datetime.now().isoformat(timespec="seconds"),
            },
        ]
        handoff_analysis = deterministic_analysis(handoff_bundle)

        def evaluate_intent(text: str) -> dict[str, Any]:
            probe = evaluation_bundle("S00019")
            probe["messages"] = [
                *probe["messages"],
                {
                    "message_id": f"EVAL-INTENT-{abs(hash(text))}",
                    "role": "customer",
                    "text": text,
                    "content_type": "text",
                    "sent_at": datetime.now().isoformat(timespec="seconds"),
                },
            ]
            return deterministic_analysis(probe)["primary_intent"]

        negation_intent = evaluate_intent("我不想退款，只想换一支。")
        correction_intent = evaluate_intent("不是色号问题，是收到的瓶子漏液了。")
        ambiguous_intent = evaluate_intent("退款和物流都帮我查一下。")
        out_of_scope_intent = evaluate_intent("帮我写一份周报。")

        cases = [
            {
                "id": "cross_system_conflict",
                "name": "跨系统色号冲突",
                "case": "S00018",
                "dimension": "风险识别",
                "passed": conflict_detected,
                "expected": "识别 #05 用户需求与 #01 工单的冲突",
            },
            {
                "id": "no_repeat_image",
                "name": "已上传图片不重复索要",
                "case": "S00001",
                "dimension": "回复安全",
                "passed": not repeat_check["passed"],
                "expected": "拦截重复索要照片的回复",
            },
            {
                "id": "medical_guard",
                "name": "不良反应表述护栏",
                "case": "S00082",
                "dimension": "回复安全",
                "passed": not medical_check["passed"],
                "expected": "拦截诊断和恢复时间承诺",
            },
            {
                "id": "emotion_escalation",
                "name": "显式愤怒升级",
                "case": "S00018 + 测试轮",
                "dimension": "情绪判断",
                "passed": anger_analysis["emotion_state"]["value"] == "愤怒",
                "expected": "情绪从不满升级为愤怒",
            },
            {
                "id": "emotion_recovery",
                "name": "最新满意反馈覆盖历史不满",
                "case": "S00018 + 测试轮",
                "dimension": "情绪判断",
                "passed": satisfaction_analysis["emotion_state"]["value"] == "满意",
                "expected": "识别情绪缓和，不被较早负面消息覆盖",
            },
            {
                "id": "resolution_guard",
                "name": "满意不等于问题已解决",
                "case": "S00018 + 测试轮",
                "dimension": "风险识别",
                "passed": satisfaction_analysis["resolution_state"]["score"] < 100,
                "expected": "色号冲突未关闭时，解决进度不会满分",
            },
            {
                "id": "intent_identification",
                "name": "产品咨询意图识别",
                "case": "S00019",
                "dimension": "意图识别",
                "passed": consultation_analysis["primary_intent"]["value"] == "色号选择",
                "expected": "从多轮对话中识别当前诉求为色号选择",
            },
            {
                "id": "evidence_bound_profile",
                "name": "有证据的服务记忆",
                "case": "S00019",
                "dimension": "服务记忆",
                "passed": "暖黄皮" in profile_values and any(value.startswith("#05") for value in profile_values),
                "expected": "只记录用户主动提供的肤质和色号偏好",
            },
            {
                "id": "latest_turn_intent",
                "name": "最新诉求覆盖历史场景",
                "case": "S00018 + 物流追问",
                "dimension": "意图识别",
                "passed": logistics_analysis["primary_intent"]["category"] == "物流服务",
                "expected": "历史为错发场景时，仍识别最新物流追问",
            },
            {
                "id": "explicit_resolution",
                "name": "用户确认后动态完成",
                "case": "S00018 + 已解决确认",
                "dimension": "服务状态",
                "passed": resolved_analysis["resolution_state"]["score"] == 100,
                "expected": "明确说没问题后，解决进度更新为 100%",
            },
            {
                "id": "human_handoff",
                "name": "主动要求人工转接",
                "case": "S00019 + 转人工",
                "dimension": "服务路由",
                "passed": handoff_analysis["service_route"]["mode"] == "human",
                "expected": "用户要求人工时停止自动回复并转人工",
            },
            {
                "id": "intent_negation_scope",
                "name": "否定范围识别",
                "case": "不想退款，只想换货",
                "dimension": "意图识别",
                "passed": negation_intent["value"] == "申请退换货",
                "expected": "排除被否定的退款意图，识别真实换货诉求",
            },
            {
                "id": "intent_correction",
                "name": "转折纠错识别",
                "case": "不是色号，是瓶子漏液",
                "dimension": "意图识别",
                "passed": correction_intent["value"] == "处理商品破损",
                "expected": "识别转折后的真实问题，不沿用前半句",
            },
            {
                "id": "intent_ambiguity_fallback",
                "name": "多意图低置信兜底",
                "case": "退款和物流都要查",
                "dimension": "意图识别",
                "passed": bool(ambiguous_intent.get("requires_clarification")),
                "expected": "候选接近时先澄清，不自动猜一个处理",
            },
            {
                "id": "intent_out_of_scope",
                "name": "超范围意图识别",
                "case": "与客服无关的请求",
                "dimension": "意图识别",
                "passed": out_of_scope_intent["category"] == "待确认",
                "expected": "未知请求进入待确认，不误套历史业务场景",
            },
        ]
        passed = sum(1 for item in cases if item["passed"])
        dimensions: dict[str, dict[str, int]] = {}
        for item in cases:
            dimension = dimensions.setdefault(item["dimension"], {"passed": 0, "total": 0})
            dimension["total"] += 1
            dimension["passed"] += int(item["passed"])

        with database(self.settings.database_path) as connection:
            latest_run = _dict(
                connection.execute(
                    """
                    SELECT provider, model, input_tokens, output_tokens, latency_ms, updated_at
                    FROM analysis_cache
                    WHERE provider = 'qwen'
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            )

        rag_started = time.perf_counter()
        rag_probe = search_knowledge(
            self.settings.database_path,
            query="错发色号后如何核对订单和补发工单，避免错误承诺",
            scene="补发换货",
            limit=3,
        )
        rag_latency_ms = round((time.perf_counter() - rag_started) * 1000, 2)
        sample_trace = build_skill_trace(
            conflict_bundle,
            conflict_analysis,
            task="draft",
            model_called=self.ai.configured,
            retrieval_count=len(rag_probe),
        )

        return {
            "suite": {
                "passed": passed,
                "total": len(cases),
                "pass_rate": round(passed / len(cases), 4),
                "note": "可复算的核心场景回归集，不代表大规模线上准确率。",
            },
            "cases": cases,
            "dimensions": dimensions,
            "cost_controls": {
                "analysis_cache": True,
                "singleflight_per_session": True,
                "draft_deduplication": True,
                "context_strategy": "首条问题 + 最近 12 条 + 结构化订单/工单/记忆",
                "max_chat_messages_per_analysis": 13,
                "on_demand_model_routing": True,
                "model_split": (
                    f"文本 {self.settings.qwen_text_model} / "
                    f"图片 {self.settings.qwen_omni_model}"
                ),
                "intent_review_context": "最新消息 + 上一条用户消息 + Top 3 候选",
                "parallel_multimodal": True,
                "local_first_response": True,
                "fast_auto_reply": True,
            },
            "architecture": {
                "strategy": "层级意图候选 + 低置信兜底 + 按需 Agent 编排",
                "skill_catalog": list(SKILL_CATALOG),
                "sample_trace": sample_trace,
            },
            "rag": {
                "method": "BM25 + 本地 N-gram 向量 + 元数据过滤 + RRF 融合",
                "graph_scope": "客户—订单—工单—SKU 关系由 SQLite 实时关联",
                "latency_ms": rag_latency_ms,
                "retrieved": [
                    {
                        "document_id": item["document_id"],
                        "document_name": item["document_name"],
                        "source_type": item["source_type"],
                        "score": item["score"],
                        "retrieval": item["retrieval"],
                    }
                    for item in rag_probe
                ],
            },
            "latest_qwen_run": latest_run,
        }

    def list_conversations(self, search: str = "", limit: int = 80) -> list[dict[str, Any]]:
        search = search.strip()
        query = """
            SELECT c.*,
                   (SELECT text FROM messages m WHERE m.session_id = c.session_id ORDER BY message_seq DESC LIMIT 1) AS preview,
                   (SELECT role FROM messages m WHERE m.session_id = c.session_id ORDER BY message_seq DESC LIMIT 1) AS latest_role,
                   (SELECT COUNT(*) FROM risk_events r WHERE r.session_id = c.session_id AND r.status != '已关闭') AS open_risks,
                   (SELECT COUNT(*) FROM commitments p WHERE p.session_id = c.session_id AND p.status != '已关闭') AS open_commitments,
                   COALESCE(s.unread_count, 0) AS unread_count,
                   COALESCE(s.service_mode, 'human') AS service_mode,
                   s.handoff_reason
            FROM conversations c
            LEFT JOIN conversation_states s ON s.session_id = c.session_id
        """
        params: list[Any] = []
        if search:
            query += " WHERE c.session_id LIKE ? OR c.buyer_nickname LIKE ? OR c.scene_minor LIKE ? OR EXISTS (SELECT 1 FROM messages sm WHERE sm.session_id = c.session_id AND sm.text LIKE ?)"
            value = f"%{search}%"
            params.extend([value, value, value, value])
        query += " ORDER BY CASE WHEN c.session_id IN ('S00018','S00001','S00082') THEN 0 ELSE 1 END, c.last_message_at DESC LIMIT ?"
        params.append(min(max(limit, 1), 200))
        with database(self.settings.database_path) as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_bundle(self, session_id: str) -> dict[str, Any]:
        with database(self.settings.database_path) as connection:
            conversation = _dict(
                connection.execute(
                    "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
                ).fetchone()
            )
            if not conversation:
                raise KeyError(session_id)
            messages = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY message_seq, sent_at",
                    (session_id,),
                ).fetchall()
            ]
            order = _dict(
                connection.execute(
                    "SELECT * FROM orders WHERE session_id = ? ORDER BY ordered_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            )
            tickets = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM tickets WHERE session_id = ? ORDER BY created_at",
                    (session_id,),
                ).fetchall()
            ]
            commitments = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM commitments WHERE session_id = ? ORDER BY created_at",
                    (session_id,),
                ).fetchall()
            ]
            service_state = _dict(
                connection.execute(
                    "SELECT * FROM conversation_states WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            )
        for message in messages:
            message["image_url"] = self._message_image_url(message)
        return {
            "conversation": conversation,
            "messages": messages,
            "order": order,
            "tickets": tickets,
            "commitments": commitments,
            "service_state": service_state
            or {
                "session_id": session_id,
                "unread_count": 0,
                "service_mode": "human",
                "handoff_reason": None,
                "last_customer_seq": 0,
                "last_auto_replied_seq": 0,
            },
        }

    def mark_conversation_read(self, session_id: str) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        with database(self.settings.database_path) as connection:
            cursor = connection.execute(
                "UPDATE conversation_states SET unread_count = 0, updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
            total = connection.execute(
                "SELECT COALESCE(SUM(unread_count), 0) AS value FROM conversation_states"
            ).fetchone()["value"]
        return {"session_id": session_id, "unread_count": 0, "total_unread": total}

    def set_service_mode(
        self, session_id: str, mode: str, reason: str | None = None
    ) -> dict[str, Any]:
        if mode not in {"ai", "human"}:
            raise ValueError("接待模式只能是 ai 或 human")
        now = datetime.now().isoformat(timespec="seconds")
        handoff_reason = reason if mode == "human" else None
        with database(self.settings.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE conversation_states
                SET service_mode = ?, handoff_reason = ?,
                    last_auto_replied_seq = CASE
                        WHEN ? = 'ai' THEN last_customer_seq
                        ELSE last_auto_replied_seq
                    END,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (mode, handoff_reason, mode, now, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(session_id)
            connection.execute(
                "INSERT INTO action_log(session_id, action_type, actor, payload_json, created_at) VALUES (?, '切换接待模式', ?, ?, ?)",
                (
                    session_id,
                    "系统" if reason else "林小稚",
                    json.dumps({"mode": mode, "reason": reason}, ensure_ascii=False),
                    now,
                ),
            )
            state = _dict(
                connection.execute(
                    "SELECT * FROM conversation_states WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            )
        return state or {}

    def handle_incoming(self, session_id: str) -> dict[str, Any]:
        """Analyze a customer turn, auto-answer low-risk AI sessions, or hand off."""
        analyzed = self.analyze(session_id, force=True)
        analysis = analyzed["analysis"]
        route = analysis.get("service_route") or {}
        bundle = analyzed["bundle"]
        state = bundle.get("service_state") or {}
        latest_customer = next(
            (item for item in reversed(bundle["messages"]) if item["role"] == "customer"),
            {},
        )
        customer_seq = int(latest_customer.get("message_seq") or 0)

        if route.get("mode") == "human":
            reason = str(route.get("reason") or "需要人工承接")
            if state.get("service_mode") != "human" or state.get("handoff_reason") != reason:
                state = self.set_service_mode(session_id, "human", reason)
            return {"status": "handed_off", "service_state": state, "analysis": analysis}

        if state.get("service_mode") != "ai":
            return {"status": "waiting_for_human", "service_state": state, "analysis": analysis}
        if int(state.get("last_auto_replied_seq") or 0) >= customer_seq:
            return {"status": "already_handled", "service_state": state, "analysis": analysis}

        generated = self.draft(session_id, tone="自然", prefer_fast=True)
        sent = self.send(session_id, generated["reply_draft"], "暖心客服")
        if sent.get("status") != "sent":
            state = self.set_service_mode(session_id, "human", "AI 回复需要人工复核")
            return {"status": "handed_off", "service_state": state, "analysis": analysis}

        now = datetime.now().isoformat(timespec="seconds")
        with database(self.settings.database_path) as connection:
            connection.execute(
                """
                UPDATE conversation_states
                SET unread_count = 0, last_auto_replied_seq = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (customer_seq, now, session_id),
            )
            state = _dict(
                connection.execute(
                    "SELECT * FROM conversation_states WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            )
        return {
            "status": "auto_replied",
            "message_id": sent["message_id"],
            "service_state": state,
            "analysis": analysis,
        }

    def _message_image_path(self, message: dict[str, Any]) -> Path | None:
        if not message.get("image_available") or not message.get("image_path"):
            return None
        stored = Path(str(message["image_path"]))
        candidates = [stored] if stored.is_absolute() else [
            self.settings.uploads_dir / stored.name,
            self.settings.workbook_path.parent / stored,
        ]
        return next((path for path in candidates if path.is_file()), None)

    def _message_image_url(self, message: dict[str, Any]) -> str | None:
        path = self._message_image_path(message)
        if not path:
            return None
        try:
            if path.parent.resolve() == self.settings.uploads_dir.resolve():
                return f"/uploads/{path.name}"
        except OSError:
            return None
        return None

    def _multimodal_inputs(self, bundle: dict[str, Any]) -> list[dict[str, str]]:
        inputs: list[dict[str, str]] = []
        for message in reversed(bundle["messages"]):
            path = self._message_image_path(message)
            if not path:
                continue
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            if mime not in IMAGE_EXTENSIONS:
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            inputs.append(
                {
                    "message_id": str(message["message_id"]),
                    "mime_type": mime,
                    "data_url": f"data:{mime};base64,{encoded}",
                }
            )
            if len(inputs) >= 2:
                break
        return list(reversed(inputs))

    def _save_uploaded_image(self, image_data_url: str) -> str:
        match = IMAGE_DATA_URL.fullmatch(image_data_url.strip())
        if not match:
            raise ValueError("仅支持 JPG、PNG 或 WebP 图片")
        mime_type, encoded = match.groups()
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("图片数据无法读取，请重新选择") from error
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise ValueError("图片需小于 4 MB")
        signatures = {
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        }
        if not signatures[mime_type]:
            raise ValueError("图片格式与文件内容不一致")
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{IMAGE_EXTENSIONS[mime_type]}"
        (self.settings.uploads_dir / filename).write_bytes(content)
        return filename

    def get_consumer_view(self, session_id: str) -> dict[str, Any]:
        bundle = self.get_bundle(session_id)
        conversation = bundle["conversation"]
        order = bundle.get("order") or {}
        return {
            "conversation": {
                "session_id": conversation["session_id"],
                "buyer_nickname": conversation["buyer_nickname"],
                "shop": conversation.get("shop"),
                "scene_minor": conversation.get("scene_minor"),
            },
            "messages": [
                {
                    "message_id": item["message_id"],
                    "message_seq": item["message_seq"],
                    "sent_at": item["sent_at"],
                    "role": item["role"],
                    "sender": item["sender"],
                    "text": item["text"],
                    "content_type": item["content_type"],
                    "image_available": bool(item.get("image_available")),
                    "image_url": item.get("image_url"),
                }
                for item in bundle["messages"]
            ],
            "order": (
                {
                    "order_id": order.get("order_id"),
                    "product_name": order.get("product_name"),
                    "sku": order.get("sku"),
                    "status": order.get("status"),
                    "carrier": order.get("carrier"),
                    "tracking_no": order.get("tracking_no"),
                    "paid_amount": order.get("paid_amount"),
                }
                if order
                else None
            ),
            "service_state": {
                "service_mode": bundle["service_state"].get("service_mode", "human"),
                "handoff_reason": bundle["service_state"].get("handoff_reason"),
            },
        }

    def _model_context(self, bundle: dict[str, Any]) -> dict[str, Any]:
        order = bundle.get("order")
        tickets = bundle.get("tickets", [])
        messages = bundle["messages"]
        selected_messages = messages if len(messages) <= 13 else [messages[0], *messages[-12:]]
        multimodal_inputs = self._multimodal_inputs(bundle)
        deterministic = deterministic_analysis(bundle)
        return {
            "conversation": {
                key: bundle["conversation"].get(key)
                for key in ("session_id", "buyer_nickname", "scene_major", "scene_minor")
            },
            "context_policy": {
                "total_messages": len(messages),
                "included_messages": len(selected_messages),
                "strategy": "full" if len(messages) <= 13 else "first_and_recent_12",
            },
            "service_graph": build_service_graph(bundle),
            "service_features": build_skill_trace(
                bundle,
                deterministic,
                task="analysis",
                model_called=False,
            )["features"],
            "chat_history": [
                {
                    "message_id": item["message_id"],
                    "role": item["role"],
                    "text": item["text"],
                    "sent_at": item["sent_at"],
                    "content_type": item["content_type"],
                    "has_viewable_image": bool(item.get("image_url")),
                }
                for item in selected_messages
            ],
            "multimodal_images": [
                {
                    "message_id": item["message_id"],
                    "content_order": index + 1,
                    "mime_type": item["mime_type"],
                }
                for index, item in enumerate(multimodal_inputs)
            ],
            "order_info": (
                {
                    key: order.get(key)
                    for key in (
                        "order_id",
                        "sku",
                        "product_name",
                        "status",
                        "carrier",
                        "tracking_no",
                        "ordered_at",
                        "shipped_at",
                    )
                }
                if order
                else None
            ),
            "ticket_info": [
                {
                    key: ticket.get(key)
                    for key in (
                        "ticket_id",
                        "ticket_kind",
                        "category",
                        "reason",
                        "status",
                        "owner",
                        "product_sku",
                        "product_name",
                        "tracking_no",
                        "batch_no",
                        "created_at",
                        "completed_at",
                    )
                }
                for ticket in tickets
            ],
            "commitments": [
                {
                    key: commitment.get(key)
                    for key in ("content", "deadline", "status", "evidence_id")
                }
                for commitment in bundle.get("commitments", [])
            ],
        }

    def _valid_evidence_ids(self, bundle: dict[str, Any]) -> set[str]:
        ids = {message["message_id"] for message in bundle["messages"]}
        if bundle.get("order"):
            ids.add(bundle["order"]["order_id"])
        ids.update(ticket["ticket_id"] for ticket in bundle.get("tickets", []))
        return ids

    @staticmethod
    def _refresh_cached_live_state(
        cached_result: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any]:
        refreshed = dict(cached_result)
        for key in ("primary_intent", "emotion_state", "resolution_state", "service_route"):
            refreshed[key] = current[key]

        intent = current.get("primary_intent") or {}
        if intent.get("source") in {
            "latest_turn",
            "latest_turn_ambiguous",
            "out_of_scope",
        }:
            for key in (
                "summary",
                "next_actions",
                "service_stage",
                "intent_shift",
                "risk_level",
                "risk_signals",
            ):
                refreshed[key] = current[key]
        return refreshed

    def _sanitize_model_analysis(
        self,
        bundle: dict[str, Any],
        deterministic: dict[str, Any],
        model_result: dict[str, Any],
    ) -> dict[str, Any]:
        valid_ids = self._valid_evidence_ids(bundle)
        merged = dict(deterministic)
        for key in (
            "primary_intent",
            "secondary_intents",
            "service_stage",
            "emotion_state",
            "summary",
            "next_actions",
            "visual_observations",
        ):
            if key in model_result and model_result[key] not in (None, "", []):
                merged[key] = model_result[key]

        def clean_evidence(value: Any) -> Any:
            if isinstance(value, dict):
                cleaned = {key: clean_evidence(item) for key, item in value.items()}
                if "evidence" in cleaned and isinstance(cleaned["evidence"], list):
                    normalized_ids = []
                    for item in cleaned["evidence"]:
                        candidate = (
                            item.get("id")
                            or item.get("message_id")
                            or item.get("order_id")
                            or item.get("ticket_id")
                            if isinstance(item, dict)
                            else item
                        )
                        if candidate is not None and str(candidate) in valid_ids:
                            normalized_ids.append(str(candidate))
                    cleaned["evidence"] = normalized_ids
                return cleaned
            if isinstance(value, list):
                return [clean_evidence(item) for item in value]
            return value

        merged = clean_evidence(merged)
        model_intent = merged.get("primary_intent") or {}
        deterministic_intent = deterministic.get("primary_intent") or {}
        if not is_allowed_intent(model_intent):
            merged["primary_intent"] = deterministic["primary_intent"]
        elif deterministic_intent.get("source") == "latest_turn":
            merged["primary_intent"] = deterministic_intent
            for key in ("summary", "next_actions", "service_stage", "intent_shift"):
                merged[key] = deterministic[key]
        else:
            merged["primary_intent"] = {
                **model_intent,
                "source": "qwen_context",
                "requires_clarification": bool(
                    model_intent.get("requires_clarification")
                    or model_intent.get("category") == "待确认"
                ),
            }
        merged["resolution_state"] = deterministic["resolution_state"]
        merged["service_route"] = deterministic["service_route"]
        visual_observations = []
        for item in merged.get("visual_observations") or []:
            if not isinstance(item, dict):
                continue
            message_id = str(item.get("message_id") or "")
            if message_id not in valid_ids:
                continue
            observation_type = str(item.get("type") or "other")
            if observation_type not in {
                "product_label",
                "package_damage",
                "skin_condition",
                "other",
            }:
                observation_type = "other"
            finding = str(item.get("finding") or "").strip()
            if not finding:
                continue
            try:
                confidence = min(max(float(item.get("confidence") or 0), 0), 1)
            except (TypeError, ValueError):
                confidence = 0
            visual_observations.append(
                {
                    "message_id": message_id,
                    "type": observation_type,
                    "finding": finding,
                    "comparison": str(item.get("comparison") or "").strip(),
                    "confidence": round(confidence, 2),
                    "requires_review": bool(item.get("requires_review")),
                    "limitation": str(item.get("limitation") or "").strip(),
                }
            )
        merged["visual_observations"] = visual_observations
        emotion_rank = {"平稳": 0, "满意": 0, "着急": 1, "担心": 1, "不满": 2, "愤怒": 3}
        deterministic_emotion = deterministic.get("emotion_state") or {}
        model_emotion = merged.get("emotion_state") or {}
        if deterministic_emotion.get("trend") == "情绪已缓和":
            merged["emotion_state"] = deterministic_emotion
        elif emotion_rank.get(model_emotion.get("value"), 0) < emotion_rank.get(
            deterministic_emotion.get("value"), 0
        ):
            merged["emotion_state"] = deterministic_emotion
        visual_risks = []
        for observation in visual_observations:
            if not observation["requires_review"]:
                continue
            visual_risks.append(
                {
                    "risk_key": f"visual_review:{observation['message_id']}",
                    "type": "visual_review",
                    "severity": "high" if observation["type"] == "skin_condition" else "medium",
                    "title": "图片信息需要人工复核",
                    "detail": observation["comparison"] or observation["finding"],
                    "evidence": [
                        {
                            "id": observation["message_id"],
                            "source_type": "聊天图片",
                            "label": "图片核对结果",
                            "value": observation["finding"],
                            "source_row": 0,
                        }
                    ],
                }
            )
        merged["risk_signals"] = [*deterministic["risk_signals"], *visual_risks]
        merged["risk_level"] = (
            "high"
            if any(item["severity"] == "high" for item in merged["risk_signals"])
            else "medium"
            if merged["risk_signals"]
            else "none"
        )
        merged["memory"] = [
            *deterministic["memory"],
            *[
                {
                    "kind": "service_fact",
                    "label": "图片核对",
                    "value": observation["finding"],
                    "status": "needs_review" if observation["requires_review"] else "confirmed",
                    "source_id": observation["message_id"],
                    "source_type": "聊天图片",
                    "event_time": None,
                }
                for observation in visual_observations
            ],
        ]
        merged["customer_profile"] = deterministic["customer_profile"]
        merged["intent_shift"] = deterministic["intent_shift"]
        merged["source"] = "qwen+rules"
        return merged

    def snapshot(self, session_id: str) -> dict[str, Any]:
        bundle = self.get_bundle(session_id)
        with database(self.settings.database_path) as connection:
            cached = connection.execute(
                "SELECT * FROM analysis_cache WHERE session_id = ?", (session_id,)
            ).fetchone()
        if cached:
            result = json.loads(cached["result_json"])
            current = deterministic_analysis(bundle)
            result = self._refresh_cached_live_state(result, current)
            result["run"] = {
                "provider": cached["provider"],
                "model": cached["model"],
                "input_tokens": cached["input_tokens"],
                "output_tokens": cached["output_tokens"],
                "latency_ms": cached["latency_ms"],
                "cached": True,
                "pending": False,
            }
            result.setdefault(
                "orchestration",
                build_skill_trace(
                    bundle,
                    result,
                    task="analysis",
                    model_called=cached["provider"] == "qwen",
                    cached=True,
                ),
            )
        else:
            result = deterministic_analysis(bundle)
            result["run"] = {
                "provider": "instant",
                "model": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": 0,
                "cached": False,
                "pending": self.ai.configured
                and should_use_understanding_model(bundle, result),
            }
            result["orchestration"] = build_skill_trace(
                bundle,
                result,
                task="analysis",
                model_called=False,
            )
        return {"bundle": bundle, "analysis": result}

    def analyze(self, session_id: str, force: bool = False) -> dict[str, Any]:
        with self._analysis_lock(session_id):
            return self._analyze_unlocked(session_id, force=force)

    def _analyze_unlocked(self, session_id: str, force: bool = False) -> dict[str, Any]:
        bundle = self.get_bundle(session_id)
        if not force:
            with database(self.settings.database_path) as connection:
                cached = connection.execute(
                    "SELECT * FROM analysis_cache WHERE session_id = ?", (session_id,)
                ).fetchone()
            if cached:
                result = json.loads(cached["result_json"])
                current = deterministic_analysis(bundle)
                result = self._refresh_cached_live_state(result, current)
                result["run"] = {
                    "provider": cached["provider"],
                    "model": cached["model"],
                    "input_tokens": cached["input_tokens"],
                    "output_tokens": cached["output_tokens"],
                    "latency_ms": cached["latency_ms"],
                    "cached": True,
                }
                result.setdefault(
                    "orchestration",
                    build_skill_trace(
                        bundle,
                        result,
                        task="analysis",
                        model_called=cached["provider"] == "qwen",
                        cached=True,
                    ),
                )
                return {"bundle": bundle, "analysis": result}

        deterministic = deterministic_analysis(bundle)
        metadata = {
            "provider": "local",
            "model": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": 0,
        }
        result = deterministic
        use_model = self.ai.configured and should_use_understanding_model(
            bundle, deterministic
        )
        if use_model:
            try:
                multimodal_inputs = self._multimodal_inputs(bundle)
                model_context = self._model_context(bundle)
                visual_result: dict[str, Any] = {"visual_observations": []}
                visual_metadata: dict[str, Any] | None = None
                model_result: dict[str, Any] = {}
                text_metadata: dict[str, Any] | None = None
                intent = deterministic.get("primary_intent") or {}
                needs_intent_review = bool(
                    intent.get("requires_clarification")
                    or intent.get("source")
                    in {"out_of_scope", "latest_turn_ambiguous"}
                    or float(intent.get("confidence") or 0) < 0.7
                )
                needs_full_text_analysis = bool(
                    not needs_intent_review
                    and not is_simple_turn(bundle)
                    and deterministic.get("risk_level") in {"high", "medium"}
                )

                customers = [
                    item
                    for item in bundle.get("messages", [])
                    if item.get("role") == "customer"
                ]

                def analyze_text() -> tuple[dict[str, Any], dict[str, Any]]:
                    if needs_intent_review:
                        return self.ai.review_intent(
                            customers[-1] if customers else {},
                            customers[-2] if len(customers) > 1 else None,
                            intent,
                        )
                    return self.ai.analyze(model_context, deterministic)

                run_text = needs_intent_review or needs_full_text_analysis
                if multimodal_inputs and run_text:
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        text_future = executor.submit(analyze_text)
                        visual_future = executor.submit(
                            self.ai.inspect_images,
                            model_context,
                            [item["data_url"] for item in multimodal_inputs],
                        )
                        model_result, text_metadata = text_future.result()
                        visual_result, visual_metadata = visual_future.result()
                elif multimodal_inputs:
                    visual_result, visual_metadata = self.ai.inspect_images(
                        model_context,
                        image_urls=[item["data_url"] for item in multimodal_inputs],
                    )
                elif run_text:
                    model_result, text_metadata = analyze_text()

                model_result["visual_observations"] = visual_result.get(
                    "visual_observations", []
                )
                if text_metadata:
                    metadata = text_metadata
                if visual_metadata and text_metadata:
                    metadata = {
                        **text_metadata,
                        "model": (
                            f"{text_metadata.get('model')} + "
                            f"{visual_metadata.get('model')}"
                        ),
                        "models": {
                            "text": text_metadata.get("model"),
                            "vision": visual_metadata.get("model"),
                        },
                        "input_tokens": (text_metadata.get("input_tokens") or 0)
                        + (visual_metadata.get("input_tokens") or 0),
                        "output_tokens": (text_metadata.get("output_tokens") or 0)
                        + (visual_metadata.get("output_tokens") or 0),
                        "latency_ms": max(
                            text_metadata.get("latency_ms") or 0,
                            visual_metadata.get("latency_ms") or 0,
                        ),
                        "multimodal": True,
                    }
                elif visual_metadata:
                    metadata = {**visual_metadata, "multimodal": True}
                result = self._sanitize_model_analysis(bundle, deterministic, model_result)
            except Exception as error:
                metadata = {
                    **metadata,
                    "provider": "local-fallback",
                    "error": type(error).__name__,
                    "error_detail": str(error),
                }

        result["run"] = {**metadata, "cached": False}
        result["orchestration"] = build_skill_trace(
            bundle,
            result,
            task="analysis",
            model_called=metadata["provider"] == "qwen",
        )
        with database(self.settings.database_path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO analysis_cache(
                    session_id, result_json, provider, model, input_tokens,
                    output_tokens, latency_ms, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    json.dumps(result, ensure_ascii=False),
                    metadata["provider"],
                    metadata.get("model"),
                    metadata.get("input_tokens"),
                    metadata.get("output_tokens"),
                    metadata.get("latency_ms"),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        self._upsert_risks(bundle, result["risk_signals"])
        return {"bundle": bundle, "analysis": result}

    def draft(
        self, session_id: str, tone: str = "自然", prefer_fast: bool = False
    ) -> dict[str, Any]:
        analyzed = self.analyze(session_id)
        bundle = analyzed["bundle"]
        analysis = analyzed["analysis"]
        query = " ".join(
            [bundle["conversation"].get("scene_major") or "", bundle["conversation"].get("scene_minor") or ""]
            + [message["text"] for message in bundle["messages"][-6:]]
            + [
                " ".join(
                    [
                        observation.get("finding") or "",
                        observation.get("comparison") or "",
                    ]
                )
                for observation in analysis.get("visual_observations", [])
            ]
        )
        knowledge = (
            search_knowledge(
                self.settings.database_path,
                query=query,
                product_id=(bundle.get("order") or {}).get("sku"),
                scene=bundle["conversation"].get("scene_major"),
                limit=4,
            )
            if should_retrieve_knowledge(bundle)
            else []
        )
        fallback = fallback_reply(bundle, analysis, tone)
        if not self.ai.configured or prefer_fast:
            return {
                **fallback,
                "provider": "local-fast-path" if prefer_fast else fallback.get("provider", "rules"),
                "knowledge": knowledge,
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="draft",
                    model_called=False,
                    retrieval_count=len(knowledge),
                ),
            }
        try:
            model_result, metadata = self.ai.draft(
                self._model_context(bundle), analysis, knowledge, tone
            )
            reply_draft = model_result.get("reply_draft") or fallback["reply_draft"]
            preflight = deterministic_quality_check(bundle, analysis, reply_draft)
            if not preflight["passed"]:
                return {
                    **fallback,
                    "provider": "qwen-guarded",
                    "model": metadata["model"],
                    "latency_ms": metadata["latency_ms"],
                    "guard_issues": preflight["issues"],
                    "knowledge": knowledge,
                    "orchestration": build_skill_trace(
                        bundle,
                        analysis,
                        task="draft",
                        model_called=True,
                        retrieval_count=len(knowledge),
                    ),
                }
            valid_ids = self._valid_evidence_ids(bundle)
            used_evidence = [
                str(item)
                for item in model_result.get("used_evidence", [])
                if not isinstance(item, (dict, list)) and str(item) in valid_ids
            ]
            return {
                "reply_draft": reply_draft,
                "tags": model_result.get("tags") or fallback["tags"],
                "used_evidence": used_evidence or fallback["used_evidence"],
                "commitments": model_result.get("commitments") or [],
                "provider": metadata["provider"],
                "model": metadata["model"],
                "latency_ms": metadata["latency_ms"],
                "input_tokens": metadata.get("input_tokens"),
                "output_tokens": metadata.get("output_tokens"),
                "knowledge": knowledge,
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="draft",
                    model_called=True,
                    retrieval_count=len(knowledge),
                ),
            }
        except Exception as error:
            return {
                **fallback,
                "provider": "local-fallback",
                "fallback_reason": type(error).__name__,
                "knowledge": knowledge,
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="draft",
                    model_called=False,
                    retrieval_count=len(knowledge),
                ),
            }

    def quality_check(self, session_id: str, text: str) -> dict[str, Any]:
        analyzed = self.snapshot(session_id)
        bundle = analyzed["bundle"]
        analysis = analyzed["analysis"]
        rule_result = deterministic_quality_check(bundle, analysis, text)
        if not self.ai.configured or not rule_result["passed"]:
            return {
                **rule_result,
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="quality",
                    model_called=False,
                ),
            }
        needs_deep_check = len(text) > 120 or any(
            term in text
            for term in (
                "退款",
                "赔偿",
                "补发",
                "发出",
                "到账",
                "保证",
                "确保",
                "过敏",
                "皮炎",
                "恢复",
            )
        )
        if not needs_deep_check:
            return {
                **rule_result,
                "provider": "rules-fastpath",
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="quality",
                    model_called=False,
                ),
            }
        try:
            model_result, metadata = self.ai.quality_check(
                self._model_context(bundle), analysis, text, rule_result
            )
            combined = {issue["code"]: issue for issue in rule_result["issues"]}
            for issue in model_result.get("issues", []):
                if isinstance(issue, dict) and issue.get("code"):
                    advisory = dict(issue)
                    advisory["severity"] = "medium"
                    combined.setdefault(advisory["code"], advisory)
            issues = list(combined.values())
            return {
                "passed": rule_result["passed"],
                "issues": issues,
                "checks": rule_result["checks"],
                "suggested_rewrite": model_result.get("suggested_rewrite", ""),
                **metadata,
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="quality",
                    model_called=True,
                ),
            }
        except Exception as error:
            return {
                **rule_result,
                "provider": "rules-fallback",
                "fallback_reason": type(error).__name__,
                "orchestration": build_skill_trace(
                    bundle,
                    analysis,
                    task="quality",
                    model_called=False,
                ),
            }

    def send(self, session_id: str, text: str, actor: str) -> dict[str, Any]:
        quality = self.quality_check(session_id, text)
        if not quality["passed"]:
            return {"status": "blocked", "quality": quality}

        message_id = f"LOCAL-{uuid.uuid4().hex[:12].upper()}"
        sent_at = datetime.now().isoformat(timespec="seconds")
        with database(self.settings.database_path) as connection:
            maximum = connection.execute(
                "SELECT COALESCE(MAX(message_seq), 0) AS value FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["value"]
            connection.execute(
                """
                INSERT INTO messages(
                    message_id, session_id, message_seq, sent_at, role, sender,
                    text, content_type, source_sheet, source_row
                ) VALUES (?, ?, ?, ?, 'agent', ?, ?, 'text', '本地演示', 0)
                """,
                (message_id, session_id, maximum + 1, sent_at, actor, text),
            )
            connection.execute(
                "UPDATE conversations SET message_count = message_count + 1, last_message_at = ? WHERE session_id = ?",
                (sent_at, session_id),
            )
            if actor != "暖心客服":
                connection.execute(
                    """
                    UPDATE conversation_states
                    SET service_mode = 'human', handoff_reason = '客服已接管', updated_at = ?
                    WHERE session_id = ?
                    """,
                    (sent_at, session_id),
                )
            if actor != "暖心客服":
                connection.execute(
                    "DELETE FROM analysis_cache WHERE session_id = ?", (session_id,)
                )
            connection.execute(
                "INSERT INTO action_log(session_id, action_type, actor, payload_json, created_at) VALUES (?, '发送回复', ?, ?, ?)",
                (session_id, actor, json.dumps({"message_id": message_id}, ensure_ascii=False), sent_at),
            )
            for commitment in extract_commitments(text):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO commitments(
                        session_id, content, deadline, evidence_id, source_message_id,
                        owner, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '处理中', ?, ?)
                    """,
                    (
                        session_id,
                        commitment["content"],
                        commitment["deadline"],
                        message_id,
                        message_id,
                        actor,
                        sent_at,
                        sent_at,
                    ),
                )
        return {"status": "sent", "message_id": message_id, "sent_at": sent_at, "quality": quality}

    def add_incoming(
        self,
        session_id: str,
        text: str,
        *,
        analyze: bool = True,
        image_data_url: str | None = None,
        image_name: str | None = None,
    ) -> dict[str, Any]:
        bundle = self.get_bundle(session_id)
        normalized_text = text.strip()
        image_path = self._save_uploaded_image(image_data_url) if image_data_url else None
        if not normalized_text:
            normalized_text = "请帮我看一下这张图片。" if image_path else ""
        message_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"
        sent_at = datetime.now().isoformat(timespec="seconds")
        with database(self.settings.database_path) as connection:
            maximum = connection.execute(
                "SELECT COALESCE(MAX(message_seq), 0) AS value FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["value"]
            connection.execute(
                """
                INSERT INTO messages(
                    message_id, session_id, message_seq, sent_at, role, sender,
                    text, content_type, image_path, image_available, source_sheet, source_row
                ) VALUES (?, ?, ?, ?, 'customer', ?, ?, ?, ?, ?, '本地演示', 0)
                """,
                (
                    message_id,
                    session_id,
                    maximum + 1,
                    sent_at,
                    bundle["conversation"]["buyer_nickname"],
                    normalized_text,
                    "image" if image_path else "text",
                    image_path,
                    int(bool(image_path)),
                ),
            )
            connection.execute(
                "UPDATE conversations SET message_count = message_count + 1, last_message_at = ? WHERE session_id = ?",
                (sent_at, session_id),
            )
            connection.execute(
                """
                INSERT INTO conversation_states(
                    session_id, unread_count, service_mode, handoff_reason,
                    last_customer_seq, last_auto_replied_seq, updated_at
                ) VALUES (?, 1, 'human', NULL, ?, 0, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    unread_count = conversation_states.unread_count + 1,
                    last_customer_seq = excluded.last_customer_seq,
                    updated_at = excluded.updated_at
                """,
                (session_id, maximum + 1, sent_at),
            )
            connection.execute("DELETE FROM analysis_cache WHERE session_id = ?", (session_id,))
        if analyze:
            result = self.analyze(session_id, force=True)
            return {"message_id": message_id, "sent_at": sent_at, **result}

        updated_bundle = self.get_bundle(session_id)
        deterministic = deterministic_analysis(updated_bundle)
        self._upsert_risks(updated_bundle, deterministic["risk_signals"])
        return {
            "status": "received",
            "message_id": message_id,
            "sent_at": sent_at,
        }

    def _upsert_risks(self, bundle: dict[str, Any], risks: list[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        session_id = bundle["conversation"]["session_id"]
        active_keys = [str(item["risk_key"]) for item in risks]
        with database(self.settings.database_path) as connection:
            if active_keys:
                placeholders = ",".join("?" for _ in active_keys)
                connection.execute(
                    f"""
                    UPDATE risk_events
                    SET status = '已关闭', updated_at = ?
                    WHERE session_id = ? AND risk_type != 'adverse_reaction'
                      AND status != '已关闭' AND risk_key NOT IN ({placeholders})
                    """,
                    (now, session_id, *active_keys),
                )
            else:
                connection.execute(
                    """
                    UPDATE risk_events
                    SET status = '已关闭', updated_at = ?
                    WHERE session_id = ? AND risk_type != 'adverse_reaction'
                      AND status != '已关闭'
                    """,
                    (now, session_id),
                )
            for risk in risks:
                deadline = (
                    datetime.now() + timedelta(hours=24 if risk["type"] == "adverse_reaction" else 4)
                ).isoformat(timespec="seconds")
                connection.execute(
                    """
                    INSERT INTO risk_events(
                        session_id, risk_key, risk_type, severity, title, detail,
                        evidence_json, owner, deadline, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, '待处理', ?, ?)
                    ON CONFLICT(session_id, risk_key) DO UPDATE SET
                        title = excluded.title,
                        detail = excluded.detail,
                        evidence_json = excluded.evidence_json,
                        status = CASE
                            WHEN risk_events.status = '已关闭' THEN '待处理'
                            ELSE risk_events.status
                        END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        bundle["conversation"]["session_id"],
                        risk["risk_key"],
                        risk["type"],
                        risk["severity"],
                        risk["title"],
                        risk["detail"],
                        json.dumps(risk["evidence"], ensure_ascii=False),
                        deadline,
                        now,
                        now,
                    ),
                )

    def seed_derived_records(self) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with database(self.settings.database_path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO conversation_states(
                    session_id, unread_count, service_mode, handoff_reason,
                    last_customer_seq, last_auto_replied_seq, updated_at
                )
                SELECT session_id, 0,
                       CASE WHEN session_id = 'S00019' THEN 'ai' ELSE 'human' END,
                       NULL,
                       COALESCE((SELECT MAX(message_seq) FROM messages m WHERE m.session_id = conversations.session_id AND m.role = 'customer'), 0),
                       0,
                       ?
                FROM conversations
                """,
                (now,),
            )
            sessions = [row["session_id"] for row in connection.execute("SELECT session_id FROM conversations")]
        for session_id in sessions:
            bundle = self.get_bundle(session_id)
            analysis = deterministic_analysis(bundle)
            self._upsert_risks(bundle, analysis["risk_signals"])
            with database(self.settings.database_path) as connection:
                for message in bundle["messages"]:
                    if message["role"] != "agent":
                        continue
                    try:
                        message_time = datetime.fromisoformat(message["sent_at"]) if message.get("sent_at") else datetime.now()
                    except ValueError:
                        message_time = datetime.now()
                    for commitment in extract_commitments(message["text"], now=message_time):
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO commitments(
                                session_id, content, deadline, evidence_id, source_message_id,
                                owner, status, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, '处理中', ?, ?)
                            """,
                            (
                                session_id,
                                commitment["content"],
                                commitment["deadline"],
                                message["message_id"],
                                message["message_id"],
                                "待分配",
                                message.get("sent_at") or now,
                                now,
                            ),
                        )

    def list_risks(self, status: str = "open") -> dict[str, Any]:
        where = "WHERE r.status != '已关闭'" if status == "open" else ""
        with database(self.settings.database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, c.buyer_nickname, c.scene_major, c.scene_minor
                FROM risk_events r
                JOIN conversations c ON c.session_id = r.session_id
                {where}
                """
            ).fetchall()
            commitments = connection.execute(
                """
                SELECT p.*, c.buyer_nickname
                FROM commitments p
                JOIN conversations c ON c.session_id = p.session_id
                WHERE p.status != '已关闭'
                ORDER BY p.deadline
                """
            ).fetchall()
        risks = []
        for row in rows:
            item = dict(row)
            item["evidence"] = json.loads(item.pop("evidence_json"))
            risks.append(item)
        risks.sort(key=lambda item: (_severity_rank(item["severity"]), item.get("deadline") or ""))
        return {
            "risks": risks,
            "commitments": [dict(row) for row in commitments],
            "summary": {
                "open": len(risks),
                "high": sum(item["severity"] == "high" for item in risks),
                "unassigned": sum(not item.get("owner") for item in risks),
                "commitments": len(commitments),
            },
        }

    def update_risk(
        self,
        risk_id: int,
        owner: str | None,
        deadline: str | None,
        status: str | None,
    ) -> dict[str, Any]:
        fields: list[str] = []
        values: list[Any] = []
        for key, value in (("owner", owner), ("deadline", deadline), ("status", status)):
            if value is not None:
                fields.append(f"{key} = ?")
                values.append(value)
        if not fields:
            raise ValueError("没有可更新的字段")
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        values.append(risk_id)
        with database(self.settings.database_path) as connection:
            cursor = connection.execute(
                f"UPDATE risk_events SET {', '.join(fields)} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise KeyError(risk_id)
            row = connection.execute("SELECT * FROM risk_events WHERE id = ?", (risk_id,)).fetchone()
            connection.execute(
                "INSERT INTO action_log(session_id, action_type, actor, payload_json, created_at) VALUES (?, '更新风险', ?, ?, ?)",
                (
                    row["session_id"],
                    owner or "系统",
                    json.dumps({"risk_id": risk_id, "status": status, "deadline": deadline}, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    def get_evidence(self, evidence_id: str) -> dict[str, Any]:
        with database(self.settings.database_path) as connection:
            message = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (evidence_id,)
            ).fetchone()
            if message:
                item = dict(message)
                return {
                    "id": evidence_id,
                    "source_type": "聊天",
                    "source_sheet": item["source_sheet"],
                    "source_row": item["source_row"],
                    "title": f"聊天消息 {item['message_seq']}",
                    "content": item["text"],
                    "media_url": self._message_image_url(item),
                    "fields": item,
                }
            order = connection.execute(
                "SELECT * FROM orders WHERE order_id = ?", (evidence_id,)
            ).fetchone()
            if order:
                item = dict(order)
                return {
                    "id": evidence_id,
                    "source_type": "订单",
                    "source_sheet": item["source_sheet"],
                    "source_row": item["source_row"],
                    "title": item.get("product_name") or "订单记录",
                    "content": f"{item.get('product_name') or ''} / {item.get('status') or ''}",
                    "fields": item,
                }
            ticket = connection.execute(
                "SELECT * FROM tickets WHERE ticket_id = ?", (evidence_id,)
            ).fetchone()
            if ticket:
                item = dict(ticket)
                return {
                    "id": evidence_id,
                    "source_type": item["source_sheet"],
                    "source_sheet": item["source_sheet"],
                    "source_row": item["source_row"],
                    "title": f"{item.get('ticket_kind') or ''}工单 {evidence_id}",
                    "content": " / ".join(
                        value
                        for value in (
                            item.get("category"),
                            item.get("reason"),
                            item.get("product_name"),
                            item.get("status"),
                        )
                        if value
                    ),
                    "fields": item,
                }
        raise KeyError(evidence_id)
