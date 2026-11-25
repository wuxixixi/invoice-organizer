"""SQLite 存储层：连接管理、建表、事务、各实体 CRUD、批次快照、审计追加。

设计约束：
- 所有落库必须经过 Database（ARCHITECTURE 1.3）；
- ``audit_events`` append-only，无删除接口；
- 配置版本 profile 表存储全量快照，历史不删；
- 引用 ``sqlite3.Row`` 便于按列名读取。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from invoice_organizer import ERROR_CODES
from invoice_organizer.models.audit import AuditEvent
from invoice_organizer.models.batch import Batch, BatchReport
from invoice_organizer.models.invoice import FieldSlot, InvoiceRecord
from invoice_organizer.models.plan import Conflict, Plan, PlanItem
from invoice_organizer.storage.migrations import Migrations
from invoice_organizer.utils.logging import get_logger


def _new_id(prefix: str) -> str:
    """生成带前缀的唯一 ID。"""
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# SQLite 列为 snake_case，领域模型 from_dict() 使用 camelCase。
# 以下映射函数集中完成 row→模型 转换，避免读取后字段退化为默认值。
# ---------------------------------------------------------------------------
def _invoice_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> InvoiceRecord:
    """把 invoices 行映射为 InvoiceRecord，并加载其字段槽位。"""
    data = dict(row)
    mapped: dict[str, Any] = {
        "id": data.get("id", ""),
        "invoiceNo": data.get("invoice_no", "") or "",
        "issueDate": data.get("issue_date", "") or "",
        "amountCent": data.get("amount_cent", 0) or 0,
        "categoryLabel": data.get("category_label", "") or "",
        "categoryRuleId": data.get("category_rule_id") or None,
        "status": data.get("status", "NEW") or "NEW",
        "fields": [],
        "items": [],
        "sources": [],
    }
    inv = InvoiceRecord.from_dict(mapped)
    # batch_id 不是 dataclass 声明字段，save_invoice 通过 getattr 读取，保留该动态属性
    setattr(inv, "batch_id", data.get("batch_id", "") or "")
    slots = conn.execute(
        "SELECT * FROM field_slots WHERE invoice_id=? ORDER BY rowid", (inv.id,)
    ).fetchall()
    inv.fields = [
        FieldSlot.from_dict({
            "key": s["field_key"],
            "value": s["value"],
            "confidence": s["confidence"],
            "evidence": json.loads(s["evidence_json"] or "{}"),
            "verified": bool(s["verified"]),
        })
        for s in slots
    ]
    # 行项目与来源引用以 JSON 列持久化，读取时还原
    from invoice_organizer.models.invoice import ItemLine, SourceRef

    try:
        inv.items = [ItemLine(
            name=str(d.get("name", "")), spec=str(d.get("spec", "")),
            quantity=int(d.get("quantity", 1) or 1),
            amountCent=int(d.get("amountCent", 0) or 0),
        ) for d in json.loads(data.get("items_json") or "[]")]
        inv.sources = [SourceRef.from_dict(d)
                       for d in json.loads(data.get("sources_json") or "[]")]
    except (ValueError, TypeError):  # 旧库/损坏 JSON 时降级为空，不阻断读取
        inv.items = []
        inv.sources = []
    return inv


def _batch_from_row(row: sqlite3.Row) -> Batch:
    """把 batches 行映射为 Batch（含配置快照/统计/报告）。"""
    data = dict(row)
    report_raw = data.get("report_json") or ""
    return Batch.from_dict({
        "id": data.get("id", ""),
        "profileVersion": data.get("profile_version", 1) or 1,
        "configSnapshot": data.get("config_snapshot", "") or "",
        "status": data.get("status", "RUNNING") or "RUNNING",
        "summary": json.loads(data.get("summary_json") or "{}"),
        "startedAt": data.get("started_at", "") or "",
        "endedAt": data.get("ended_at", "") or "",
        "report": json.loads(report_raw) if report_raw else None,
    })


def _plan_from_row(row: sqlite3.Row) -> Plan:
    """把 plans 行映射为 Plan（计划项在 get_plan 中单独加载）。"""
    data = dict(row)
    return Plan.from_dict({
        "id": data.get("id", ""),
        "batchId": data.get("batch_id", "") or "",
        "status": data.get("status", "DRAFT") or "DRAFT",
        "profileVersion": data.get("profile_version", 1) or 1,
        "createdAt": data.get("created_at", "") or "",
        "items": [],
    })


class Database:
    """本地 SQLite 元数据库。"""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """初始化数据库（不自动建表，需显式调用 ``init_schema``）。

        Args:
            db_path: 数据库文件路径；``":memory:"`` 表示内存库（测试用）。
        """
        self.dbPath = str(db_path)
        if self.dbPath != ":memory:":
            parent = Path(self.dbPath).parent
            parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()

    # ---------------------------------------------------------------- 连接
    def connect(self) -> sqlite3.Connection:
        """返回当前线程的连接（线程局部，避免跨线程共享 cursor）。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.dbPath, timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        """建表并迁移到当前 schema 版本（幂等）。

        始终以 ``SCHEMA_VERSION`` 为目标；旧库即使已标记 ``user_version=1``，
        ``Migrations.migrate`` 仍会执行幂等列补丁，补齐后续追加的列。
        """
        conn = self.connect()
        with self._init_lock:
            Migrations.migrate(conn)
            conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：正常提交，异常回滚。"""
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------ profiles
    def save_profile(self, profile_id: str, version: int, schema_version: int,
                     config_json: str, is_active: bool = True) -> None:
        """保存一个配置版本（历史不删，同 profile_id+version 覆盖）。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO profiles(profile_id,version,schema_version,config_json,is_active)
               VALUES(?,?,?,?,?)
               ON CONFLICT(profile_id) DO UPDATE SET
                 version=excluded.version, schema_version=excluded.schema_version,
                 config_json=excluded.config_json, is_active=excluded.is_active""",
            (profile_id, version, schema_version, config_json, int(is_active)),
        )
        conn.commit()

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        """读取档案最新配置记录。"""
        row = self.connect().execute(
            "SELECT * FROM profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
        return dict(row) if row else None

    # --------------------------------------------------------------- files
    def insert_file(self, sha256: str, path: str, size: int, mtime_ns: int,
                    status: str = "NEW") -> bool:
        """记录文件哈希；已存在返回 False（去重依据）。"""
        conn = self.connect()
        cur = conn.execute(
            """INSERT OR IGNORE INTO files(sha256,path,size,mtime_ns,first_seen_at,status)
               VALUES(?,?,?,?,datetime('now'),?)""",
            (sha256, path, size, mtime_ns, status),
        )
        conn.commit()
        return cur.rowcount > 0

    def find_file_by_hash(self, sha256: str) -> dict[str, Any] | None:
        """按内容哈希查找文件记录。"""
        row = self.connect().execute(
            "SELECT * FROM files WHERE sha256=?", (sha256,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ invoices
    def save_invoice(self, inv: InvoiceRecord) -> None:
        """保存发票记录及其八字段槽位。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO invoices(id,batch_id,invoice_no,issue_date,amount_cent,
                                     category_label,category_rule_id,items_json,
                                     sources_json,status)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 invoice_no=excluded.invoice_no, issue_date=excluded.issue_date,
                 amount_cent=excluded.amount_cent, category_label=excluded.category_label,
                 category_rule_id=excluded.category_rule_id,
                 items_json=excluded.items_json, sources_json=excluded.sources_json,
                 status=excluded.status""",
            (inv.id, getattr(inv, "batch_id", "") or "", inv.invoiceNo, inv.issueDate,
             inv.amountCent, inv.categoryLabel, inv.categoryRuleId or "",
             json.dumps([{"name": i.name, "spec": i.spec, "quantity": i.quantity,
                          "amountCent": i.amountCent} for i in inv.items], ensure_ascii=False),
             json.dumps([s.to_dict() for s in inv.sources], ensure_ascii=False),
             inv.status),
        )
        conn.execute("DELETE FROM field_slots WHERE invoice_id=?", (inv.id,))
        for slot in inv.fields:
            conn.execute(
                """INSERT INTO field_slots(invoice_id,field_key,value,confidence,
                                            evidence_json,verified)
                   VALUES(?,?,?,?,?,?)""",
                (inv.id, slot.key, slot.value, slot.confidence.value,
                 json.dumps(slot.evidence.to_dict(), ensure_ascii=False),
                 int(slot.verified)),
            )
        conn.commit()

    def get_invoice(self, invoice_id: str) -> InvoiceRecord | None:
        """按 ID 读取发票（含字段槽位）。"""
        conn = self.connect()
        row = conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        return _invoice_from_row(conn, row) if row else None

    def list_invoices(self, batch_id: str = "") -> list[InvoiceRecord]:
        """列出发票（可按批次过滤）；每条均加载字段槽位。"""
        conn = self.connect()
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM invoices WHERE batch_id=? ORDER BY rowid", (batch_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM invoices ORDER BY rowid").fetchall()
        return [_invoice_from_row(conn, row) for row in rows]

    def find_invoice_by_no(self, invoice_no: str) -> InvoiceRecord | None:
        """按票号查找发票（冲突判定用）。"""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM invoices WHERE invoice_no=? ORDER BY rowid LIMIT 1", (invoice_no,)
        ).fetchone()
        return _invoice_from_row(conn, row) if row else None

    # -------------------------------------------------------------- batches
    def save_batch(self, batch: Batch) -> None:
        """保存/更新批次（含配置快照与报告）。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO batches(id,profile_version,config_snapshot,status,summary_json,
                                    started_at,ended_at,report_json)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 profile_version=excluded.profile_version,
                 config_snapshot=excluded.config_snapshot, status=excluded.status,
                 summary_json=excluded.summary_json, started_at=excluded.started_at,
                 ended_at=excluded.ended_at, report_json=excluded.report_json""",
            (batch.id, batch.profileVersion, batch.configSnapshot, batch.status.value,
             json.dumps(batch.summary.to_dict(), ensure_ascii=False),
             batch.startedAt, batch.endedAt,
             json.dumps(batch.report.to_dict(), ensure_ascii=False) if batch.report else ""),
        )
        conn.commit()

    def get_batch(self, batch_id: str) -> Batch | None:
        """按 ID 读取批次。"""
        row = self.connect().execute(
            "SELECT * FROM batches WHERE id=?", (batch_id,)
        ).fetchone()
        return _batch_from_row(row) if row else None

    def list_batches(self) -> list[Batch]:
        """列出全部批次（按开始时间倒序）。"""
        rows = self.connect().execute(
            "SELECT * FROM batches ORDER BY started_at DESC"
        ).fetchall()
        return [_batch_from_row(row) for row in rows]

    def update_batch_status(self, batch_id: str, status: str,
                            summary: dict[str, Any] | None = None,
                            report: BatchReport | None = None,
                            ended_at: str = "") -> None:
        """仅更新批次状态/统计/报告字段。"""
        conn = self.connect()
        batch = self.get_batch(batch_id)
        if not batch:
            return
        summary_json = json.dumps(summary, ensure_ascii=False) if summary else None
        report_json = json.dumps(report.to_dict(), ensure_ascii=False) if report else None
        if summary_json is not None and report_json is not None:
            conn.execute(
                """UPDATE batches SET status=?, summary_json=?, report_json=?, ended_at=?
                   WHERE id=?""",
                (status, summary_json, report_json, ended_at, batch_id),
            )
        elif summary_json is not None:
            conn.execute(
                "UPDATE batches SET status=?, summary_json=?, ended_at=? WHERE id=?",
                (status, summary_json, ended_at, batch_id),
            )
        else:
            conn.execute(
                "UPDATE batches SET status=?, ended_at=? WHERE id=?",
                (status, ended_at, batch_id),
            )
        conn.commit()

    # ---------------------------------------------------------------- plans
    def save_plan(self, plan: Plan) -> None:
        """保存/更新计划及其计划项。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO plans(id,batch_id,status,profile_version,created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status, profile_version=excluded.profile_version""",
            (plan.id, plan.batchId, plan.status.value, plan.profileVersion, plan.createdAt),
        )
        conn.execute("DELETE FROM plan_items WHERE plan_id=?", (plan.id,))
        for idx, item in enumerate(plan.items):
            conn.execute(
                """INSERT INTO plan_items(plan_id,invoice_id,old_path,new_path,hit_rule_id,
                                           missing_fields_json,conflict_json,confirmed)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (plan.id, item.invoiceId, item.oldPath, item.newPath,
                 item.hitRuleId or "", json.dumps(item.missingFields, ensure_ascii=False),
                 json.dumps(item.conflict.to_dict(), ensure_ascii=False) if item.conflict else "",
                 int(item.confirmed)),
            )
        conn.commit()

    def get_plan(self, plan_id: str) -> Plan | None:
        """按 ID 读取计划（含计划项）。"""
        conn = self.connect()
        row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        plan = _plan_from_row(row)
        items = conn.execute(
            "SELECT * FROM plan_items WHERE plan_id=? ORDER BY rowid", (plan_id,)
        ).fetchall()
        plan.items = [PlanItem.from_dict({
            "invoiceId": it["invoice_id"], "oldPath": it["old_path"],
            "newPath": it["new_path"], "hitRuleId": it["hit_rule_id"] or None,
            "missingFields": json.loads(it["missing_fields_json"] or "[]"),
            "conflict": json.loads(it["conflict_json"]) if it["conflict_json"] else None,
            "confirmed": bool(it["confirmed"]),
        }) for it in items]
        return plan

    def find_plan_by_batch(self, batch_id: str) -> Plan | None:
        """按批次 ID 读取计划。"""
        row = self.connect().execute(
            "SELECT id FROM plans WHERE batch_id=? ORDER BY created_at DESC LIMIT 1", (batch_id,)
        ).fetchone()
        return self.get_plan(row["id"]) if row else None

    def update_plan_status(self, plan_id: str, status: str) -> None:
        """更新计划状态。"""
        conn = self.connect()
        conn.execute("UPDATE plans SET status=? WHERE id=?", (status, plan_id))
        conn.commit()

    def update_plan_item_confirmed(self, plan_id: str, invoice_id: str,
                                   confirmed: bool = True) -> None:
        """标记某计划项已确认。"""
        conn = self.connect()
        conn.execute(
            "UPDATE plan_items SET confirmed=? WHERE plan_id=? AND invoice_id=?",
            (int(confirmed), plan_id, invoice_id),
        )
        conn.commit()

    # ------------------------------------------------------------- archives
    def save_archive(self, res: Any) -> None:
        """保存归档结果（鸭子类型：读取 res 的字段，避免与 archiver 循环依赖）。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO archives(plan_item_id,invoice_id,source_sha256,archived_path,
                                     archived_sha256,verified,error_code)
               VALUES(?,?,?,?,?,?,?)""",
            (getattr(res, "planItemId", ""), getattr(res, "invoiceId", ""),
             getattr(res, "sourceSha256", ""), getattr(res, "archivedPath", ""),
             getattr(res, "sha256", ""), int(getattr(res, "verified", False)),
             getattr(res, "errorCode", "") or ""),
        )
        conn.commit()

    def list_archives(self, batch_id: str = "") -> list[dict[str, Any]]:
        """列出归档结果。"""
        rows = self.connect().execute(
            "SELECT * FROM archives ORDER BY rowid"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ conflicts
    def save_conflict(self, conflict: Conflict) -> None:
        """保存/更新冲突条目。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO conflicts(id,batch_id,type,invoice_no,detail_json,status,resolution)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                 resolution=excluded.resolution""",
            (conflict.id, getattr(conflict, "batchId", ""), conflict.type,
             conflict.invoiceNo, json.dumps(conflict.detail, ensure_ascii=False)
             if isinstance(conflict.detail, dict) else conflict.detail,
             conflict.status, conflict.resolution),
        )
        conn.commit()

    def list_conflicts(self, batch_id: str = "") -> list[Conflict]:
        """列出冲突（可按批次过滤）。"""
        conn = self.connect()
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM conflicts WHERE batch_id=? ORDER BY rowid", (batch_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM conflicts ORDER BY rowid").fetchall()
        result: list[Conflict] = []
        for row in rows:
            raw = dict(row)
            result.append(Conflict(
                id=raw["id"], type=raw["type"], invoiceNo=raw["invoice_no"],
                detail=raw["detail_json"], status=raw["status"],
                resolution=raw["resolution"],
            ))
        return result

    # --------------------------------------------------------- review_items
    def add_review(self, batch_id: str, invoice_id: str, field_key: str,
                   reason: str, detail: str = "") -> None:
        """追加一条待复核记录。"""
        conn = self.connect()
        conn.execute(
            """INSERT INTO review_items(batch_id,invoice_id,field_key,reason,detail,status)
               VALUES(?,?,?,?,?,'OPEN')""",
            (batch_id, invoice_id, field_key, reason, detail),
        )
        conn.commit()

    def list_review(self, batch_id: str = "") -> list[dict[str, Any]]:
        """列出待复核记录（可按批次过滤）。"""
        conn = self.connect()
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM review_items WHERE batch_id=? ORDER BY rowid", (batch_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM review_items ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]

    def resolve_review(self, review_id: int, resolution: str,
                       field_value: str = "") -> None:
        """回填待复核记录（仅更新 status/resolution，不删除，保留审计痕迹）。"""
        conn = self.connect()
        conn.execute(
            """UPDATE review_items SET status='RESOLVED', resolution=?, detail=?
               WHERE id=?""",
            (resolution, field_value, review_id),
        )
        conn.commit()

    # --------------------------------------------------------------- audit
    def add_audit(self, event: AuditEvent) -> None:
        """追加一条审计事件（append-only）。若审计写入失败将记录 E801 并抛异常。"""
        conn = self.connect()
        if not event.id:
            event.id = _new_id("A")
        try:
            conn.execute(
                """INSERT INTO audit_events(id,ts,actor,role,action,object,result,detail)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (event.id, event.ts, event.actor, event.role.value, event.action.value,
                 event.object, event.result, event.detail),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 审计失败必须可观测：记录 E801 后上抛
            get_logger().error(
                "E801 %s 审计写入失败：action=%s object=%s error=%s",
                ERROR_CODES["E801"], event.action.value, event.object, exc,
            )
            raise

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        """读取审计事件（只读，无删除）。"""
        rows = self.connect().execute(
            "SELECT * FROM audit_events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- utility
    def close(self) -> None:
        """关闭当前线程连接。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


__all__ = ["Database"]
