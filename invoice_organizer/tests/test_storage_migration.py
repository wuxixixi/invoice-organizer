"""旧版 SQLite 元数据库升级回归：缺列旧库必须原地补齐且不丢数据。

背景：修复存储往返缺陷时在 V1_STATEMENTS 中追加了
invoices.items_json / invoices.sources_json / plans.profile_version，
但 user_version 仍为 1，旧库不会重跑 CREATE TABLE，导致 save_invoice 抛
OperationalError。本 module 用真实文件库复现旧库并验证迁移路径。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from invoice_organizer.models.invoice import InvoiceRecord, ItemLine, SourceRef
from invoice_organizer.models.plan import Plan
from invoice_organizer.storage.db import Database
from invoice_organizer.storage.migrations import Migrations

# 修复前 v1 真实建表语句（仅列出本次涉及的表）：三张表均缺后续追加的列。
_LEGACY_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE invoices (
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
    CREATE TABLE field_slots (
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
    CREATE TABLE plans (
        id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'DRAFT',
        created_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE plan_items (
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
)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    """读取一张表的实际列名集合。"""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _make_legacy_v1_db(path: Path) -> None:
    """手工构造 user_version=1 且缺新增列的旧库，并写入历史数据。"""
    conn = sqlite3.connect(str(path))
    try:
        for statement in _LEGACY_V1_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            """INSERT INTO invoices(id,batch_id,invoice_no,issue_date,amount_cent,
                                     category_label,category_rule_id,status)
               VALUES('Ilegacy','Blegacy','87654321','2024-01-02',500,'差旅','R1','NEW')"""
        )
        conn.execute(
            """INSERT INTO plans(id,batch_id,status,created_at)
               VALUES('Plegacy','Blegacy','DRAFT','2024-01-02')"""
        )
        conn.commit()
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


def test_legacy_v1_db_upgrades_in_place_and_keeps_data(tmp_path: Path) -> None:
    """旧 v1 库初始化后缺列被补齐、历史数据不丢，save_invoice 不再报错。"""
    db_path = tmp_path / "legacy_v1.db"
    _make_legacy_v1_db(db_path)

    # Arrange：确认构造的确实是缺列旧库（否则用例失去意义）
    with sqlite3.connect(str(db_path)) as raw:
        assert "items_json" not in _column_names(raw, "invoices")
        assert Migrations.current_version(raw) == 1

    # Act：正常初始化路径（不应要求用户删库）
    db = Database(db_path)
    db.init_schema()
    conn = db.connect()

    # Assert：列补齐、版本不变、历史数据完整
    assert {"items_json", "sources_json"} <= _column_names(conn, "invoices")
    assert "profile_version" in _column_names(conn, "plans")
    assert Migrations.current_version(conn) == 1
    legacy = conn.execute(
        "SELECT invoice_no, issue_date, amount_cent FROM invoices WHERE id='Ilegacy'"
    ).fetchone()
    assert tuple(legacy) == ("87654321", "2024-01-02", 500)
    legacy_plan = conn.execute(
        "SELECT batch_id, status FROM plans WHERE id='Plegacy'"
    ).fetchone()
    assert tuple(legacy_plan) == ("Blegacy", "DRAFT")

    # 核心回归：旧库上的写入路径（原缺陷现场）
    inv = InvoiceRecord(id="Inew", invoiceNo="11112222", issueDate="2024-11-25",
                        amountCent=900)
    setattr(inv, "batch_id", "Blegacy")
    inv.items = [ItemLine(name="技术服务费", spec="", quantity=2, amountCent=900)]
    inv.sources = [SourceRef(fileHash="a" * 64, filePath="scan.pdf", page=1)]
    db.save_invoice(inv)

    fetched = db.get_invoice("Inew")
    assert fetched is not None
    assert fetched.invoiceNo == "11112222"
    assert len(fetched.items) == 1 and fetched.items[0].name == "技术服务费"
    assert fetched.items[0].quantity == 2
    assert len(fetched.sources) == 1 and fetched.sources[0].fileHash == "a" * 64

    # plans.profile_version 同样是修复 A 追加的列，旧库上也必须可写可读
    db.save_plan(Plan(id="Pnew", batchId="Blegacy", profileVersion=7))
    loaded = db.get_plan("Pnew")
    assert loaded is not None and loaded.profileVersion == 7
    db.close()


def test_legacy_column_patch_is_idempotent(tmp_path: Path) -> None:
    """重复初始化与重复列补丁不报错、不产生重复列。"""
    db_path = tmp_path / "legacy_v1.db"
    _make_legacy_v1_db(db_path)

    db = Database(db_path)
    db.init_schema()
    conn = db.connect()
    assert Migrations.ensure_columns(conn) == []

    db.init_schema()
    assert Migrations.ensure_columns(conn) == []
    invoice_cols = [row[1] for row in conn.execute("PRAGMA table_info(invoices)")]
    assert invoice_cols.count("items_json") == 1
    assert invoice_cols.count("sources_json") == 1
    db.close()


def test_fresh_database_keeps_create_table_path(tmp_path: Path) -> None:
    """新库仍走原 CREATE TABLE 路径，11 张表齐全且无需列补丁。"""
    db = Database(tmp_path / "fresh.db")
    db.init_schema()
    conn = db.connect()

    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {
        "profiles", "files", "invoices", "field_slots", "batches", "plans",
        "plan_items", "archives", "conflicts", "review_items", "audit_events",
    } <= tables
    assert Migrations.current_version(conn) == 1
    assert Migrations.ensure_columns(conn) == []
    db.close()
