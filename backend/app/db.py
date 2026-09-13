from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    record_key TEXT,
    payload_json TEXT NOT NULL,
    UNIQUE(source_sheet, source_row)
);

CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT PRIMARY KEY,
    buyer_nickname TEXT NOT NULL,
    shop TEXT,
    scene_major TEXT,
    scene_minor TEXT,
    started_at TEXT,
    last_message_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    source_sheet TEXT NOT NULL DEFAULT '聊天记录'
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
    message_seq INTEGER NOT NULL,
    sent_at TEXT,
    role TEXT NOT NULL CHECK(role IN ('customer', 'agent')),
    sender TEXT,
    text TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'text',
    image_path TEXT,
    image_available INTEGER NOT NULL DEFAULT 0,
    order_id TEXT,
    ticket_id TEXT,
    source_sheet TEXT NOT NULL DEFAULT '聊天记录',
    source_row INTEGER NOT NULL,
    UNIQUE(session_id, message_seq)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_time
ON messages(session_id, message_seq, sent_at);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES conversations(session_id) ON DELETE SET NULL,
    buyer_nickname TEXT,
    shop TEXT,
    sku TEXT,
    product_name TEXT,
    quantity INTEGER,
    unit_price REAL,
    paid_amount REAL,
    status TEXT,
    ordered_at TEXT,
    paid_at TEXT,
    shipped_at TEXT,
    carrier TEXT,
    tracking_no TEXT,
    province TEXT,
    city TEXT,
    gift TEXT,
    buyer_note TEXT,
    source_sheet TEXT NOT NULL DEFAULT '订单',
    source_row INTEGER NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_id);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES conversations(session_id) ON DELETE SET NULL,
    order_id TEXT,
    buyer_nickname TEXT,
    shop TEXT,
    ticket_kind TEXT NOT NULL,
    category TEXT,
    reason TEXT,
    status TEXT,
    owner TEXT,
    created_at TEXT,
    completed_at TEXT,
    product_sku TEXT,
    product_name TEXT,
    tracking_no TEXT,
    batch_no TEXT,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_session ON tickets(session_id);
CREATE INDEX IF NOT EXISTS idx_tickets_order ON tickets(order_id);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    product_id TEXT,
    scene TEXT,
    authority INTEGER NOT NULL DEFAULT 1,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL,
    source_url TEXT,
    license TEXT
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    session_id TEXT PRIMARY KEY REFERENCES conversations(session_id) ON DELETE CASCADE,
    result_json TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    deadline TEXT,
    evidence_id TEXT,
    source_message_id TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT '待确认',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, content, source_message_id)
);

CREATE INDEX IF NOT EXISTS idx_commitments_status_deadline
ON commitments(status, deadline);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
    risk_key TEXT NOT NULL,
    risk_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('high', 'medium')),
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    owner TEXT,
    deadline TEXT,
    status TEXT NOT NULL DEFAULT '待处理',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, risk_key)
);

CREATE INDEX IF NOT EXISTS idx_risks_status_severity
ON risk_events(status, severity, deadline);

CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    action_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_documents)")
        }
        migrations = {
            "scene": "TEXT",
            "authority": "INTEGER NOT NULL DEFAULT 1",
            "source_url": "TEXT",
            "license": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE knowledge_documents ADD COLUMN {column} {definition}"
                )


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
