"""schema 版本迁移框架与 v0→v1 初始迁移。"""
from __future__ import annotations

import sqlite3

from invoice_organizer import SCHEMA_VERSION

# v1 初始迁移：11 张表（ARCHITECTURE 3.4）
V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS profiles (
        profile_id TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        schema_version INTEGER NOT NULL DEFAULT 1,
        config_json TEXT NOT NULL DEFAULT '{}',
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sha256 TEXT NOT NULL UNIQUE,
        path TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL DEFAULT 0,
        mtime_ns INTEGER NOT NULL DEFAULT 0,
        first_seen_at TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'NEW'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS invoices (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL DEFAULT '',
        invoice_no TEXT NOT NULL DEFAULT '',
        issue_date TEXT NOT NULL DEFAULT '',
        amount_cent INTEGER NOT NULL DEFAULT 0,
        category_label TEXT NOT NULL DEFAULT '',
        category_rule_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'NEW'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS field_slots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_id TEXT NOT NULL,
        field_key TEXT NOT NULL,
        value TEXT NOT NULL DEFAULT '',
        confidence TEXT NOT NULL DEFAULT 'need_human',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        verified INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batches (
        id TEXT PRIMARY KEY,
        profile_version INTEGER NOT NULL DEFAULT 1,
        config_snapshot TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'RUNNING',
        summary_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL DEFAULT '',
        ended_at TEXT NOT NULL DEFAULT '',
        report_json TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'DRAFT',
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id TEXT NOT NULL,
        invoice_id TEXT NOT NULL DEFAULT '',
        old_path TEXT NOT NULL DEFAULT '',
        new_path TEXT NOT NULL DEFAULT '',
        hit_rule_id TEXT NOT NULL DEFAULT '',
        missing_fields_json TEXT NOT NULL DEFAULT '[]',
        conflict_json TEXT NOT NULL DEFAULT '',
        confirmed INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS archives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_item_id TEXT NOT NULL DEFAULT '',
        invoice_id TEXT NOT NULL DEFAULT '',
        source_sha256 TEXT NOT NULL DEFAULT '',
        archived_path TEXT NOT NULL DEFAULT '',
        archived_sha256 TEXT NOT NULL DEFAULT '',
        verified INTEGER NOT NULL DEFAULT 0,
        error_code TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conflicts (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL DEFAULT '',
        type TEXT NOT NULL DEFAULT '',
        invoice_no TEXT NOT NULL DEFAULT '',
        detail_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'OPEN',
        resolution TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL DEFAULT '',
        invoice_id TEXT NOT NULL DEFAULT '',
        field_key TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'OPEN',
        resolution TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL DEFAULT '',
        object TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT 'OK',
        detail TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_invoices_batch ON invoices(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_invoices_no ON invoices(invoice_no)",
    "CREATE INDEX IF NOT EXISTS idx_field_slots_invoice ON field_slots(invoice_id)",
    "CREATE INDEX IF NOT EXISTS idx_plan_items_plan ON plan_items(plan_id)",
    "CREATE INDEX IF NOT EXISTS idx_review_batch ON review_items(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_archives_plan_item ON archives(plan_item_id)",
)


class Migrations:
    """基于 SQLite ``PRAGMA user_version`` 的版本迁移。"""

    @staticmethod
    def current_version(conn: sqlite3.Connection) -> int:
        """读取当前 schema 版本。"""
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def migrate(conn: sqlite3.Connection, to: int = SCHEMA_VERSION) -> None:
        """把数据库迁移到目标版本。

        Args:
            conn: SQLite 连接。
            to: 目标版本；默认当前 ``SCHEMA_VERSION``。

        Raises:
            ValueError: 目标版本低于当前版本（不支持降级）或版本未知。
        """
        current = Migrations.current_version(conn)
        if to < current:
            raise ValueError(f"不支持降级迁移：{current} -> {to}")
        if to > SCHEMA_VERSION:
            raise ValueError(f"未知目标版本：{to}（当前最高 {SCHEMA_VERSION}）")
        if current < 1 <= to:
            for statement in V1_STATEMENTS:
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 1")
        # 未来版本可在此追加 current < 2 <= to 等分支


__all__ = ["Migrations", "V1_STATEMENTS"]
