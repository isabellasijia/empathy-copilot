from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .db import database


META_PATTERN = re.compile(r"^([a-z_]+):\s*(.+)$", re.MULTILINE)
SYNONYMS = {
    "辣脸": "刺痛 灼热 不良反应",
    "烧脸": "刺痛 灼热 不良反应",
    "过敏": "不良反应 泛红 瘙痒",
    "打泥": "搓泥 使用方法",
    "发错": "错发 换货 色号",
    "漏发": "少件 补发",
    "口红": "唇膏 唇釉",
    "拔干": "干燥 唇纹 保湿",
}
VECTOR_DIMENSIONS = 384
RRF_K = 60
SCENE_SPECIFIC = {"不良反应", "补发换货", "产品咨询", "物流服务", "退款打款"}


def load_knowledge(knowledge_dir: Path, database_path: Path) -> int:
    documents: list[dict[str, Any]] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta = {match.group(1): match.group(2).strip() for match in META_PATTERN.finditer(text)}
        body = re.sub(
            r"^---\s*$.*?^---\s*$", "", text, flags=re.MULTILINE | re.DOTALL
        ).strip()
        documents.append(
            {
                "document_id": meta.get("document_id") or path.stem,
                "title": meta.get("title") or path.stem,
                "source_type": meta.get("source_type") or "客服SOP",
                "product_id": meta.get("product_id"),
                "scene": meta.get("scene"),
                "authority": max(1, min(int(meta.get("authority") or 1), 3)),
                "version": meta.get("version") or "1.0",
                "content": body,
                "keywords": meta.get("keywords") or "",
                "source_path": str(path.relative_to(knowledge_dir.parent.parent)),
                "source_url": meta.get("source_url"),
                "license": meta.get("license"),
            }
        )

    with database(database_path) as connection:
        connection.execute("DELETE FROM knowledge_documents")
        connection.executemany(
            """
            INSERT INTO knowledge_documents(
                document_id, title, source_type, product_id, scene, authority,
                version, content, keywords, source_path, source_url, license
            ) VALUES (
                :document_id, :title, :source_type, :product_id, :scene, :authority,
                :version, :content, :keywords, :source_path, :source_url, :license
            )
            """,
            documents,
        )
    return len(documents)


def _normalized_text(text: str) -> str:
    normalized = text.lower()
    for source, target in SYNONYMS.items():
        if source in normalized:
            normalized += " " + target
    return normalized


def _tokens(text: str) -> list[str]:
    normalized = _normalized_text(text)
    tokens = re.findall(r"[a-z0-9#]{2,}", normalized)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _vector(text: str) -> dict[int, float]:
    normalized = re.sub(r"\s+", "", _normalized_text(text))
    features = _tokens(text)
    features.extend(
        normalized[index : index + 3]
        for index in range(max(len(normalized) - 2, 0))
    )
    vector: dict[int, float] = {}
    for feature, count in Counter(features).items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=4).digest()
        bucket = int.from_bytes(digest, "big") % VECTOR_DIMENSIONS
        vector[bucket] = vector.get(bucket, 0.0) + 1.0 + math.log(count)
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    return {key: value / norm for key, value in vector.items()}


def _cosine(left: dict[int, float], right: dict[int, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def _bm25_scores(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    if not documents:
        return []
    count = len(documents)
    avg_length = sum(len(tokens) for tokens in documents) / count or 1.0
    frequencies = Counter(token for tokens in documents for token in set(tokens))
    scores: list[float] = []
    for tokens in documents:
        term_counts = Counter(tokens)
        score = 0.0
        for term in set(query_tokens):
            frequency = term_counts.get(term, 0)
            if not frequency:
                continue
            document_frequency = frequencies.get(term, 0)
            inverse_frequency = math.log(
                1 + (count - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + 1.2 * (
                1 - 0.75 + 0.75 * len(tokens) / avg_length
            )
            score += inverse_frequency * frequency * 2.2 / denominator
        scores.append(score)
    return scores


def search_knowledge(
    database_path: Path,
    query: str,
    product_id: str | None = None,
    scene: str | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    with database(database_path) as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM knowledge_documents")]
    if scene:
        rows = [
            row
            for row in rows
            if not row.get("scene")
            or row["scene"] not in SCENE_SPECIFIC
            or row["scene"] in scene
            or (product_id and row.get("product_id") == product_id)
        ]
    if not rows:
        return []

    query_tokens = _tokens(query)
    query_vector = _vector(query)
    document_texts = [f"{row['title']} {row['keywords']} {row['content']}" for row in rows]
    document_tokens = [_tokens(text) for text in document_texts]
    bm25 = _bm25_scores(query_tokens, document_tokens)
    vector = [_cosine(query_vector, _vector(text)) for text in document_texts]
    bm25_rank = sorted(range(len(rows)), key=lambda index: bm25[index], reverse=True)
    vector_rank = sorted(range(len(rows)), key=lambda index: vector[index], reverse=True)
    bm25_position = {index: position for position, index in enumerate(bm25_rank, start=1)}
    vector_position = {index: position for position, index in enumerate(vector_rank, start=1)}

    results = []
    for index, row in enumerate(rows):
        if bm25[index] <= 0 and vector[index] <= 0:
            continue
        rrf = 1 / (RRF_K + bm25_position[index]) + 1 / (
            RRF_K + vector_position[index]
        )
        metadata_boost = 0.0
        if product_id and row.get("product_id") == product_id:
            metadata_boost += 0.012
        if scene and row.get("scene") and row["scene"] in scene:
            metadata_boost += 0.008
        authority_boost = max(int(row.get("authority") or 1) - 1, 0) * 0.002
        results.append(
            {
                "document_id": row["document_id"],
                "document_name": row["title"],
                "product_id": row.get("product_id"),
                "scene": row.get("scene"),
                "version": row["version"],
                "content": row["content"][:520],
                "source_type": row["source_type"],
                "source_path": row["source_path"],
                "source_url": row.get("source_url"),
                "license": row.get("license"),
                "retrieval": {
                    "method": "bm25+local_ngram_vector+rrf",
                    "bm25": round(bm25[index], 4),
                    "vector": round(vector[index], 4),
                    "rrf": round(rrf + metadata_boost + authority_boost, 6),
                },
                "score": round(rrf + metadata_boost + authority_boost, 6),
            }
        )
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]
