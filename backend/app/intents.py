from __future__ import annotations

import re
from typing import Any


INTENT_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "category": "不良反应",
        "value": "不良反应处理",
        "phrases": ("过敏", "泛红", "刺痛", "灼热", "红肿", "起疹", "发痒", "烂脸", "不适"),
        "priority": 100,
    },
    {
        "category": "退款打款",
        "value": "查询退款进度",
        "phrases": ("退款", "退钱", "没到账", "未到账", "到账", "打款", "退到哪里", "原路退回"),
        "priority": 70,
    },
    {
        "category": "物流服务",
        "value": "查询物流进度",
        "phrases": ("物流", "快递", "运单", "单号", "到哪了", "到哪里", "到货", "送到", "揽收"),
        "priority": 68,
    },
    {
        "category": "物流服务",
        "value": "查询发货时间",
        "phrases": ("发货", "寄出", "什么时候发", "多久发", "几天发", "出库"),
        "priority": 67,
    },
    {
        "category": "补发换货",
        "value": "处理错发漏发",
        "phrases": ("发错", "错发", "漏发", "少发", "补发", "发来个", "发错了"),
        "priority": 75,
    },
    {
        "category": "补发换货",
        "value": "申请退换货",
        "phrases": ("退货", "换货", "换一个", "换一支", "换一件", "不要了"),
        "priority": 73,
    },
    {
        "category": "补发换货",
        "value": "处理商品破损",
        "phrases": ("破损", "碎了", "漏液", "压坏", "包装坏", "瓶子裂", "泵头坏"),
        "priority": 80,
    },
    {
        "category": "产品咨询",
        "value": "辨别商品真伪",
        "phrases": ("真假", "真伪", "正品", "防伪", "假货"),
        "priority": 60,
    },
    {
        "category": "产品咨询",
        "value": "咨询产品成分",
        "phrases": ("成分", "孕妇", "哺乳", "酒精", "香精", "A醇", "a醇", "视黄醇"),
        "priority": 60,
    },
    {
        "category": "产品咨询",
        "value": "咨询使用方法",
        "phrases": ("怎么用", "使用方法", "先用", "后用", "频率", "保质期", "开封"),
        "priority": 58,
    },
    {
        "category": "产品咨询",
        "value": "色号选择",
        "phrases": ("色号", "显白", "黄皮", "白皮", "试色", "口红颜色"),
        "priority": 61,
    },
    {
        "category": "产品咨询",
        "value": "选择适合的产品",
        "phrases": ("适合我", "肤质", "敏感肌", "混合皮", "油皮", "干皮", "唇纹"),
        "priority": 59,
    },
    {
        "category": "服务升级",
        "value": "投诉服务体验",
        "phrases": ("投诉", "差评", "敷衍", "态度", "不满意", "生气", "找平台"),
        "priority": 90,
    },
)

ALLOWED_INTENTS = {
    (definition["category"], definition["value"])
    for definition in INTENT_DEFINITIONS
} | {
    ("转人工", "需要人工帮助"),
    ("服务确认", "确认问题已解决"),
    ("服务升级", "反馈问题仍未解决"),
    ("待确认", "需要进一步确认"),
}

NEGATION_PREFIXES = ("不想", "不需要", "不要", "不用", "无需", "别", "不是", "并非", "不")
CONTRAST_MARKERS = ("但是", "不过", "而是", "只想", "其实", "实际", "改成", "还是")
CONTEXT_DEPENDENT_HINTS = ("那", "这个", "刚才", "上面", "呢", "多久", "怎么样", "怎么办", "好的", "收到", "谢谢")
SHADE_ENTITY_PATTERN = re.compile(
    r"#\s*(\d{2})([\u4e00-\u9fff]{1,6}?)?"
    r"(?=到哪里|适合|显白|显黑|怎么|会不会|什么时候|[，。！？、,.!?\s]|$)"
)
ORDER_ENTITY_PATTERN = re.compile(r"(?<!\d)(\d{16,20})(?!\d)")


def intent_catalog() -> list[dict[str, str]]:
    return [
        {"category": category, "value": value}
        for category, value in sorted(ALLOWED_INTENTS)
        if category not in {"待确认", "服务确认", "转人工"}
    ]


def is_allowed_intent(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (str(value.get("category") or ""), str(value.get("value") or "")) in ALLOWED_INTENTS


def _is_negated(text: str, start: int) -> bool:
    prefix = re.sub(r"[，。！？、,.!?\s]", "", text[max(0, start - 5) : start])
    return any(
        prefix.endswith(marker) if marker == "不" else marker in prefix
        for marker in NEGATION_PREFIXES
    )


def _contrast_position(text: str) -> int:
    return max((text.rfind(marker) for marker in CONTRAST_MARKERS), default=-1)


def _phrase_matches(text: str, phrases: tuple[str, ...]) -> list[tuple[str, int]]:
    matches: list[tuple[str, int]] = []
    for phrase in phrases:
        start = text.find(phrase)
        while start >= 0:
            if not _is_negated(text, start):
                matches.append((phrase, start))
            start = text.find(phrase, start + len(phrase))
    return matches


def contains_unnegated_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return bool(_phrase_matches(text, phrases))


def rank_turn_intents(text: str) -> list[dict[str, Any]]:
    normalized = text.strip()
    contrast_at = _contrast_position(normalized)
    candidates: list[dict[str, Any]] = []
    for definition in INTENT_DEFINITIONS:
        matches = _phrase_matches(normalized, definition["phrases"])
        if not matches:
            continue
        phrase_score = sum(1.0 + min(len(phrase), 6) * 0.08 for phrase, _ in matches)
        if any(position > contrast_at >= 0 for _, position in matches):
            phrase_score += 0.85
        candidates.append(
            {
                "category": definition["category"],
                "value": definition["value"],
                "score": round(phrase_score + definition["priority"] / 1000, 3),
                "matched_phrases": [phrase for phrase, _ in matches],
            }
        )
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def context_dependent_turn(text: str) -> bool:
    normalized = text.strip()
    return len(normalized) <= 18 and any(hint in normalized for hint in CONTEXT_DEPENDENT_HINTS)


def extract_intent_slots(text: str) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    for match in SHADE_ENTITY_PATTERN.finditer(text):
        label = f"#{match.group(1)}{match.group(2) or ''}"
        slots.append({"type": "shade", "value": label, "span": [match.start(), match.end()]})
    for match in ORDER_ENTITY_PATTERN.finditer(text):
        slots.append(
            {
                "type": "order_id",
                "value": match.group(1),
                "span": [match.start(), match.end()],
            }
        )
    return slots
