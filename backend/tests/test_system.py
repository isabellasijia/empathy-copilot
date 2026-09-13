from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.orchestration import build_service_graph, should_use_understanding_model
from app.rag import search_knowledge
from app.rules import deterministic_analysis
from app.service import EmpathyService


@pytest.fixture(scope="module")
def service(tmp_path_factory: pytest.TempPathFactory) -> EmpathyService:
    root = Path(__file__).resolve().parents[2]
    temp_dir = tmp_path_factory.mktemp("empathy-db")
    settings = Settings(
        project_root=root,
        workbook_path=root / "data" / "official-business-data.xlsx",
        database_path=temp_dir / "test.db",
        uploads_dir=temp_dir / "uploads",
        frontend_dir=root / "frontend",
        knowledge_dir=root / "data" / "knowledge",
        dashscope_api_key=None,
    )
    instance = EmpathyService(settings)
    instance.initialize(force_import=True)
    return instance


def test_etl_imports_official_workbook(service: EmpathyService) -> None:
    stats = service.stats()
    assert stats["conversations"] == 138
    assert stats["messages"] == 998
    assert stats["orders"] == 113
    assert stats["tickets"] == 80
    assert stats["image_messages"] == 29
    assert stats["available_images"] == 0


def test_s00018_detects_cross_system_product_conflict(service: EmpathyService) -> None:
    result = service.analyze("S00018", force=True)
    conflicts = [
        item
        for item in result["analysis"]["risk_signals"]
        if item["type"] == "product_mismatch"
    ]
    assert len(conflicts) == 1
    evidence_ids = {item["id"] for item in conflicts[0]["evidence"]}
    assert "6920842605036203732" in evidence_ids
    assert "BH697811247059" in evidence_ids
    assert "#05枫叶红" in conflicts[0]["detail"]
    assert "#01赤茶红" in conflicts[0]["detail"]


def test_s00082_blocks_diagnostic_reply(service: EmpathyService) -> None:
    result = service.quality_check(
        "S00082", "你这是过敏性皮炎，2天一定恢复。"
    )
    assert result["passed"] is False
    assert "medical_claim" in {item["code"] for item in result["issues"]}


def test_s00001_does_not_ask_for_image_twice(service: EmpathyService) -> None:
    result = service.quality_check(
        "S00001", "麻烦您再重新发一次破损照片。"
    )
    assert result["passed"] is False
    assert "repeat_ask" in {item["code"] for item in result["issues"]}


def test_unsupported_immediate_promise_is_blocked(service: EmpathyService) -> None:
    result = service.quality_check(
        "S00018", "我们马上安排正确色号，全程不耽误您使用。"
    )
    assert result["passed"] is False
    assert "unsupported_promise" in {item["code"] for item in result["issues"]}


def test_risk_event_can_be_assigned(service: EmpathyService) -> None:
    payload = service.list_risks()
    risk = next(item for item in payload["risks"] if item["session_id"] == "S00018")
    updated = service.update_risk(
        risk["id"], owner="测试主管", deadline=None, status="处理中"
    )
    assert updated["owner"] == "测试主管"
    assert updated["status"] == "处理中"


def test_consumer_message_is_available_to_staff(service: EmpathyService) -> None:
    result = service.add_incoming(
        "S00018", "这是消费者端发来的联调消息。", analyze=False
    )
    assert result["status"] == "received"

    consumer = service.get_consumer_view("S00018")
    staff_bundle = service.get_bundle("S00018")
    assert consumer["messages"][-1]["text"] == "这是消费者端发来的联调消息。"
    assert staff_bundle["messages"][-1]["message_id"] == result["message_id"]
    assert "tickets" not in consumer


def test_explicit_anger_escalates_emotion(service: EmpathyService) -> None:
    service.add_incoming("S00018", "我很生气，这个问题请不要再敷衍我。", analyze=False)
    result = service.analyze("S00018", force=True)
    emotion = result["analysis"]["emotion_state"]
    assert emotion["value"] == "愤怒"
    assert "升级" in emotion["trend"]


def test_latest_satisfaction_clears_earlier_negative_emotion(
    service: EmpathyService,
) -> None:
    service.add_incoming("S00018", "我对服务很满意，谢谢。", analyze=False)
    result = service.snapshot("S00018")
    emotion = result["analysis"]["emotion_state"]
    assert emotion["value"] == "满意"
    assert emotion["trend"] == "情绪已缓和"
    resolution = result["analysis"]["resolution_state"]
    assert resolution["score"] < 100
    assert resolution["stage"] == "待核对"


def test_negative_feedback_keeps_resolution_low(service: EmpathyService) -> None:
    bundle = service.get_bundle("S00019")
    bundle["messages"] = [
        *bundle["messages"],
        {
            "message_id": "TEST-NEGATIVE",
            "role": "customer",
            "text": "我很生气，这个问题根本没有解决。",
            "content_type": "text",
            "sent_at": "2026-09-09T16:00:00",
        },
    ]
    resolution = deterministic_analysis(bundle)["resolution_state"]
    assert resolution["score"] <= 28
    assert resolution["stage"] == "先安抚"


def test_customer_confirmation_can_complete_a_clear_case(
    service: EmpathyService,
) -> None:
    bundle = service.get_bundle("S00019")
    bundle["tickets"] = []
    bundle["messages"] = [
        *bundle["messages"],
        {
            "message_id": "TEST-RESOLVED",
            "role": "customer",
            "text": "问题已经解决了，确认没问题，谢谢。",
            "content_type": "text",
            "sent_at": "2026-09-09T16:05:00",
        },
    ]
    resolution = deterministic_analysis(bundle)["resolution_state"]
    assert resolution["score"] >= 90
    assert resolution["stage"] == "已解决"


def test_plain_reply_is_not_blocked_by_existing_case_risk(
    service: EmpathyService,
) -> None:
    result = service.quality_check(
        "S00018", "谢谢您的认可，之后有需要可以随时联系我们。"
    )
    assert result["passed"] is True


def test_uploaded_image_is_available_to_both_views(service: EmpathyService) -> None:
    one_pixel_png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    result = service.add_incoming(
        "S00018",
        "请帮我核对图片上的色号。",
        analyze=False,
        image_data_url=one_pixel_png,
        image_name="shade.png",
    )
    consumer = service.get_consumer_view("S00018")
    staff = service.get_bundle("S00018")
    assert consumer["messages"][-1]["image_url"].startswith("/uploads/")
    assert staff["messages"][-1]["image_url"] == consumer["messages"][-1]["image_url"]
    evidence = service.get_evidence(result["message_id"])
    assert evidence["media_url"] == consumer["messages"][-1]["image_url"]


def test_customer_profile_only_uses_service_evidence(service: EmpathyService) -> None:
    result = service.analyze("S00019", force=True)
    profile = result["analysis"]["customer_profile"]
    values = {item["value"] for item in profile["traits"]}
    assert "暖黄皮" in values
    assert any(value.startswith("#05") for value in values)
    assert all(item["evidence"] for item in profile["traits"])


def test_evaluation_suite_is_reproducible(service: EmpathyService) -> None:
    result = service.evaluation_summary()
    assert result["suite"]["passed"] == result["suite"]["total"] == 8
    assert result["dimensions"]["情绪判断"] == {"passed": 2, "total": 2}
    assert result["dimensions"]["风险识别"] == {"passed": 2, "total": 2}
    assert result["cost_controls"]["analysis_cache"] is True
    assert result["cost_controls"]["singleflight_per_session"] is True


def test_hybrid_rag_prioritizes_matching_scene(service: EmpathyService) -> None:
    results = search_knowledge(
        service.settings.database_path,
        "错发色号后核对订单和补发工单",
        scene="补发换货",
        limit=3,
    )
    assert results[0]["document_id"] == "SOP-AFTERSALE-002"
    assert results[0]["retrieval"]["method"] == "bm25+local_ngram_vector+rrf"
    assert all(item["document_id"] != "SOP-SAFETY-001" for item in results)


def test_public_knowledge_keeps_source_and_license(service: EmpathyService) -> None:
    results = search_knowledge(
        service.settings.database_path,
        "唇釉口红成分 ingredients",
        product_id="6902395682974",
        scene="产品咨询",
        limit=2,
    )
    public = next(item for item in results if item["document_id"] == "OBF-6902395682974")
    assert public["source_url"].startswith("https://world.openbeautyfacts.org/")
    assert public["license"] == "ODbL-1.0"


def test_simple_acknowledgement_skips_deep_understanding(
    service: EmpathyService,
) -> None:
    bundle = service.get_bundle("S00019")
    bundle["messages"] = [
        *bundle["messages"],
        {
            "message_id": "TEST-THANKS",
            "role": "customer",
            "text": "好的，谢谢。",
            "content_type": "text",
            "sent_at": "2026-09-10T09:00:00",
        },
    ]
    analysis = deterministic_analysis(bundle)
    assert should_use_understanding_model(bundle, analysis) is False


def test_service_graph_links_order_ticket_and_sku(service: EmpathyService) -> None:
    graph = build_service_graph(service.get_bundle("S00018"))
    relations = {edge["relation"] for edge in graph["edges"]}
    assert {"placed", "contains", "created", "handles"} <= relations


def test_draft_exposes_real_skill_and_retrieval_trace(
    service: EmpathyService,
) -> None:
    draft = service.draft("S00018")
    assert draft["knowledge"]
    assert draft["orchestration"]["task"] == "draft"
    called = {
        item["id"]
        for item in draft["orchestration"]["skills"]
        if item["status"] == "called"
    }
    assert {"order_lookup", "ticket_lookup", "risk_guard", "hybrid_knowledge"} <= called
