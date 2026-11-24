"""OrganizeEngine：全流程用例编排（监控→去重→解析→提取→分类→计划→确认→归档→导出）
与状态机。表现层只经 JsBridge 调用本引擎，不碰文件/DB。

执行闸门（ARCHITECTURE 1.4）：
- previewRequired=true 时未 confirm_plan 的计划不得进入 Archiver（E702）；
- 批次配置快照在 start() 时写入，回溯只读快照。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from invoice_organizer import ERROR_CODES
from invoice_organizer.config.loader import ProfileLoader
from invoice_organizer.config.schema import ProfileConfig
from invoice_organizer.core.archiver import Archiver, ArchiveResult
from invoice_organizer.core.dedupe import (KIND_CONFLICT_SAME_NO,
                                           KIND_DUP_SAME_HASH, DedupeResult,
                                           DedupeService)
from invoice_organizer.core.exporter import ExportEngine
from invoice_organizer.core.fields import FieldExtractor
from invoice_organizer.core.llm_client import LlmClient
from invoice_organizer.core.rules import RuleEngine
from invoice_organizer.core.template import TemplateEngine
from invoice_organizer.core.watcher import FileWatcher, StableFile
from invoice_organizer.models.audit import AuditAction, AuditEvent, Role
from invoice_organizer.models.batch import (Batch, BatchReport, BatchStatus,
                                            BatchSummary)
from invoice_organizer.models.invoice import Confidence, FieldSlot, InvoiceRecord
from invoice_organizer.models.plan import (Conflict, Patch, Plan, PlanItem,
                                           PlanStatus)
from invoice_organizer.parsers.base import ParseContext
from invoice_organizer.parsers.registry import ParserRegistry
from invoice_organizer.storage.db import Database
from invoice_organizer.utils.logging import get_logger
from invoice_organizer.utils.paths import PathGuard


def _now() -> str:
    """返回 UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _batch_id() -> str:
    """生成批次 ID：B+YYYYMMDD-HHMMSS-4 位随机（ARCHITECTURE 7.5）。"""
    now = datetime.now()
    seq = uuid.uuid4().hex[:4].upper()
    return f"B{now:%Y%m%d}-{now:%H%M%S}-{seq}"


def _resolve_llm_key(data_dir: Path) -> str:
    """从环境变量或 gitignore 的本地密钥文件读取 Key（优先环境变量）。

    密钥只留在内存中：不写回配置、不落库、不进批次快照。
    """
    from_env = os.environ.get("INVOICE_LLM_KEY", "").strip()
    if from_env:
        return from_env
    secret_path = data_dir / "secrets.local.json"
    try:
        payload = json.loads(secret_path.read_text(encoding="utf-8"))
        section = payload.get("llm") or {}
        return str(section.get("apiKey", "") or "").strip()
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


def _inject_llm_key(config: ProfileConfig, data_dir: Path,
                    allow_network: bool = True) -> None:
    """把运行时密钥注入配置对象（仅内存，不参与序列化）。

    Args:
        config: 当前配置。
        data_dir: 数据目录（本地密钥文件所在目录）。
        allow_network: False 时完全不读取密钥文件/环境变量（测试隔离）。
    """
    if not allow_network:
        return
    key = _resolve_llm_key(data_dir)
    if key:
        config.llm.apiKey = key


def _build_llm_client(config: ProfileConfig,
                      allow_network: bool = True) -> LlmClient | None:
    """按配置构造大模型客户端；未启用或禁止联网时返回 None。"""
    if not allow_network:
        return None
    llm = getattr(config, "llm", None)
    if llm is None or not getattr(llm, "enabled", True):
        return None
    return LlmClient(
        endpoint=llm.endpoint, model=llm.model, apiKey=llm.apiKey,
        timeoutSec=llm.timeoutSec, prompt=getattr(llm, "prompt", ""),
    )


class OrganizeEngine:
    """发票整理全流程编排引擎。"""

    def __init__(self, config: ProfileConfig, db: Database,
                 loader: ProfileLoader | None = None,
                 data_dir: str | Path | None = None,
                 allow_network: bool = True) -> None:
        """初始化引擎及其协作组件。

        Args:
            config: 当前配置档案。
            db: 元数据库。
            loader: 配置加载器（可选）。
            data_dir: 数据目录（导出与日志落点，默认当前目录 data）。
            allow_network: False 时不读取密钥、不构造 LlmClient；用于测试隔离，
                即使 start() 从磁盘重载 llm.enabled=True 的配置也不会真实联网。
        """
        self.config = config
        self.db = db
        self.loader = loader
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.allow_network = bool(allow_network)
        self._logger = get_logger()

        _inject_llm_key(config, self.data_dir, self.allow_network)
        self.registry = ParserRegistry(ocr_config=getattr(config, "ocr", None))
        self.dedupe = DedupeService(db)
        self.extractor = FieldExtractor(
            config, llm_client=_build_llm_client(config, self.allow_network)
        )
        self.rules = RuleEngine(config.categories)
        self.templates = TemplateEngine(config)
        self.watcher = FileWatcher(config.watch)
        self.guard = PathGuard(config.output.archiveRoot, config.output.quarantine)
        self.archiver = Archiver(self.guard, db)
        self.exporter = ExportEngine(config.export, self.data_dir / "exports")

        # 内存态：批次 -> 发票记录 / 临时计划项 / 计划
        self._batch_invoices: dict[str, list[InvoiceRecord]] = {}
        self._temp_items: dict[str, list[PlanItem]] = {}
        self._plans: dict[str, Plan] = {}
        self._current_batch_id: str = ""
        self._role = Role.OPERATOR

    # ---------------------------------------------------------------- 启动
    def start(self, profileId: str = "", start_watcher: bool = True) -> str:
        """启动监控并创建运行批次，返回批次 ID。

        Args:
            profileId: 档案 ID；为空表示使用当前配置。
            start_watcher: False 时只创建批次、不启动后台监控（受控注入场景）。
        """
        if profileId and self.loader and profileId != self.config.profileId:
            self.config = self.loader.load(profileId)
            _inject_llm_key(self.config, self.data_dir, self.allow_network)
            self.registry = ParserRegistry(ocr_config=self.config.ocr)
            self.rules = RuleEngine(self.config.categories)
            self.extractor = FieldExtractor(
                self.config,
                llm_client=_build_llm_client(self.config, self.allow_network),
            )
            self.templates = TemplateEngine(self.config)
        snapshot = self.loader.snapshot(self.config) if self.loader else self.config.to_json()
        batch = Batch(
            id=_batch_id(), profileVersion=self.config.version,
            configSnapshot=snapshot, status=BatchStatus.RUNNING,
            startedAt=_now(),
        )
        self.db.save_batch(batch)
        self._current_batch_id = batch.id
        self._batch_invoices[batch.id] = []
        if start_watcher:
            self.watcher.start(callback=self.on_stable_file)
        self._logger.info("引擎启动，批次 %s", batch.id)
        return batch.id

    # ------------------------------------------------------------ 文件处理
    def on_stable_file(self, file: StableFile) -> str:
        """处理一个写稳文件：去重 → 解析 → 提取 → 分类 → 模板 → 落库，返回错误码。

        Args:
            file: 写稳文件快照。
        """
        batch_id = self._current_batch_id or _batch_id()
        if batch_id not in self._batch_invoices:
            self._batch_invoices[batch_id] = []
        try:
            # 1) 解析（先解析以拿到票号供去重判定）
            ctx = ParseContext(fileHash=file.sha256, filePath=file.path,
                               timeoutSec=self.config.execution.ocrTimeoutSec,
                               retry=self.config.execution.ocrRetry)
            parsed = self.registry.parse(file.path, ctx)
            if parsed.is_empty():
                self._add_review(batch_id, "", "invoiceNo", "E102",
                                 f"无可用文本：{'; '.join(parsed.warnings)}")
                return "E102"

            # 2) 提取八字段
            invoice = self.extractor.extract(parsed, self.config.amountSource)

            # 多票检测与拆分（一 PDF 多票）
            detection = self.dedupe.mark_multi_invoice(parsed, file.sha256, file.path)
            invoices = [invoice]
            if detection.kind == "MULTI_INVOICE":
                chunks = self.dedupe.split_multi_invoice(parsed)
                if len(chunks) > 1:
                    invoices = [self.extractor.extract(c, self.config.amountSource)
                                for c in chunks]

            results: list[str] = []
            for inv in invoices:
                code = self._process_one(batch_id, file, inv, parsed)
                results.append(code)
            return "E000" if all(c == "E000" for c in results) else results[0]
        except Exception as exc:  # noqa: BLE001 - 单文件异常隔离
            self._logger.exception("处理文件失败 %s：%s", file.path, exc)
            self._add_review(batch_id, "", "invoiceNo", "E301", str(exc))
            return "E301"

    def _process_one(self, batch_id: str, file: StableFile,
                     invoice: InvoiceRecord, parsed: Any) -> str:
        """处理单条发票记录（去重/分类/模板/落库）。"""
        # 去重
        ded = self.dedupe.evaluate(file, invoice.invoiceNo)
        if not ded.accept:
            self._record_conflict(batch_id, ded)
            if ded.conflict is not None and ded.conflict.type == "DUP_SAME_HASH":
                return "E201"
            return "E202"
        if ded.kind == "MULTI_INVOICE":
            self._add_review(batch_id, invoice.id, "invoiceNo", "MULTI_INVOICE",
                             "一 PDF 多票已拆分，保留同源标记")

        # 分类
        label, rule_id = self.rules.classify(invoice)
        invoice.categoryLabel = label
        invoice.categoryRuleId = rule_id

        # 模板渲染（相对归档根）
        rel_dir = self.templates.render_dir(invoice, batch_id)
        filename = self.templates.render_name(invoice, batch_id)
        rel_path = f"{rel_dir}/{filename}" if rel_dir else filename

        # 落库
        setattr(invoice, "batch_id", batch_id)
        invoice.status = "PREVIEW"
        self.db.save_invoice(invoice)
        self.db.insert_file(file.sha256, file.path, file.size, file.mtimeNs)
        self._batch_invoices[batch_id].append(invoice)

        # 待复核
        for slot in invoice.review_fields():
            reason = self._reason_for(slot)
            self._add_review(batch_id, invoice.id, slot.key, reason,
                             slot.evidence.rawText)

        # 计划项（暂存于内存，build_plan 时统一落库）
        plan_item = PlanItem(
            invoiceId=invoice.id, oldPath=file.path, newPath=rel_path,
            hitRuleId=rule_id, categoryLabel=label,
            missingFields=[s.key for s in invoice.review_fields()],
        )
        self._temp_items.setdefault(batch_id, []).append(plan_item)
        return "E000"

    @staticmethod
    def _reason_for(slot: FieldSlot) -> str:
        """根据字段槽位状态给出待复核原因（错误码字符串）。"""
        if slot.confidence.value == "need_human":
            if slot.key == "issueDate":
                return "E302"
            if slot.key == "amountCent":
                return "E303"
        return "E301"

    def _record_conflict(self, batch_id: str, ded: DedupeResult) -> None:
        """记录冲突并落库。"""
        if ded.conflict is None:
            return
        setattr(ded.conflict, "batchId", batch_id)
        self.db.save_conflict(ded.conflict)
        self._logger.warning("冲突：%s %s", ded.kind, ded.reason)

    def _add_review(self, batch_id: str, invoice_id: str, field_key: str,
                    reason: str, detail: str = "") -> None:
        """追加待复核记录。"""
        self.db.add_review(batch_id, invoice_id, field_key, reason, detail)

    # ---------------------------------------------------------------- 计划
    def build_plan(self, batchId: str) -> Plan:
        """根据某批次已收集记录构建计划（DRAFT）。

        计划版本绑定批次启动时快照的 profileVersion，不随运行中配置漂移。
        """
        items: list[PlanItem] = self._temp_items.pop(batchId, [])
        batch = self.db.get_batch(batchId)
        # 优先取批次启动时的配置版本；批次缺失时回退当前版本（防御性兜底）
        version = batch.profileVersion if batch else self.config.version
        plan = Plan(
            id="P" + uuid.uuid4().hex[:16], batchId=batchId, items=items,
            status=PlanStatus.DRAFT, profileVersion=version,
            createdAt=_now(),
        )
        self.db.save_plan(plan)
        self._plans[plan.id] = plan
        if batch:
            batch.status = BatchStatus.PREVIEW
            self.db.save_batch(batch)
        return plan

    def confirm_plan(self, planId: str, patches: Sequence[Patch] | Sequence[dict[str, Any]]) -> bool:
        """人工确认计划并应用字段修正。

        Args:
            planId: 计划 ID。
            patches: 字段修正列表（dict 或 Patch）。

        Returns:
            确认成功返回 True。
        """
        plan = self._plans.get(planId) or self.db.get_plan(planId)
        if plan is None:
            return False
        normalized: list[Patch] = [
            p if isinstance(p, Patch) else Patch.from_dict(p) for p in patches
        ]
        for patch in normalized:
            inv = next((i for i in self.db.list_invoices(plan.batchId)
                        if i.id == patch.invoiceId), None)
            if inv is None:
                continue
            from invoice_organizer.utils.dates import DateUtil
            from invoice_organizer.utils.money import MoneyUtil

            value = patch.value
            if patch.fieldKey == "issueDate":
                value = DateUtil.normalize(value) or value
            if patch.fieldKey == "amountCent":
                from invoice_organizer.utils.money import MoneyUtil as M
                inv.amountCent = M.yuan_to_cent(value)
            slot = inv.get_field(patch.fieldKey)
            inv.set_field(patch.fieldKey, FieldSlot(
                key=patch.fieldKey, value=value,
                confidence=Confidence.HIGH, evidence=slot.evidence, verified=True,
            ))
            self.db.save_invoice(inv)
        plan.status = PlanStatus.CONFIRMED
        self.db.save_plan(plan)
        self.db.add_audit(AuditEvent(
            actor="operator", role=self._role, action=AuditAction.CONFIRM,
            object=planId, result="OK", detail=f"确认 {len(plan.items)} 项，修正 {len(normalized)} 处",
        ))
        self._logger.info("计划 %s 已确认", planId)
        return True

    # ---------------------------------------------------------------- 执行
    def execute_batch(self, batchId: str) -> BatchReport:
        """执行批次：归档 → 复核 → 导出 → 报告。"""
        plan = self.db.find_plan_by_batch(batchId)
        if plan is None:
            return self._failure_report(batchId, "未找到计划")
        # 执行闸门
        if self.config.execution.previewRequired and plan.status != PlanStatus.CONFIRMED:
            self._logger.warning("E702: 计划 %s 未确认", plan.id)
            return self._failure_report(
                batchId, f"E702 {ERROR_CODES['E702']}: 计划 {plan.id} 未确认"
            )

        results: list[ArchiveResult] = self.archiver.execute(plan)
        success = sum(1 for r in results if r.verified)
        failed = len(results) - success
        if failed:
            self.archiver.rollback()
            self.db.add_audit(AuditEvent(
                actor="operator", role=self._role, action=AuditAction.ROLLBACK,
                object=batchId, result="PARTIAL", detail=f"回滚 {failed} 项失败副本",
            ))

        # 导出
        invoices = self.db.list_invoices(batchId)
        archived_paths = {r.invoiceId: r.archivedPath for r in results if r.verified}
        ledger_path = self.exporter.export_ledger(invoices, self.db.get_batch(batchId),
                                                  archived_paths)
        reviews = self.db.list_review(batchId)
        review_path = self.exporter.export_review_list(reviews, batchId)

        batch = self.db.get_batch(batchId)
        conflicts = self.db.list_conflicts(batchId)
        summary = BatchSummary(
            inputCount=len(plan.items), successCount=success, failedCount=failed,
            conflictCount=sum(1 for c in conflicts if c.type == KIND_CONFLICT_SAME_NO),
            duplicateCount=sum(1 for c in conflicts if c.type == KIND_DUP_SAME_HASH),
            reviewCount=len(reviews),
        )
        report = BatchReport(
            batchId=batchId, summary=summary, configVersion=self.config.version,
            anomalies=[f"{r.errorCode}: {r.invoiceId}" for r in results if r.errorCode],
            timeRange=f"{_now()} ~ {_now()}",
        )
        report_path = self.exporter.export_report(report)
        status = BatchStatus.PARTIAL if failed else BatchStatus.SUCCESS
        self.db.update_batch_status(batchId, status.value, summary.to_dict(), report, _now())
        self.db.update_plan_status(plan.id, PlanStatus.EXECUTED.value)
        self.db.add_audit(AuditEvent(
            actor="operator", role=self._role, action=AuditAction.EXECUTE,
            object=batchId, result=status.value,
            detail=f"成功 {success} 失败 {failed}；台账 {ledger_path}；复核 {review_path}；报告 {report_path}",
        ))
        self.db.add_audit(AuditEvent(
            actor="operator", role=self._role, action=AuditAction.EXPORT,
            object=batchId, result="OK", detail=ledger_path,
        ))
        self._logger.info("批次 %s 执行完成：%s", batchId, status.value)
        return report

    def _failure_report(self, batch_id: str, reason: str) -> BatchReport:
        """构造失败报告并更新批次状态。"""
        report = BatchReport(
            batchId=batch_id, summary=BatchSummary(), configVersion=self.config.version,
            anomalies=[reason], timeRange=_now(),
        )
        self.db.update_batch_status(batch_id, BatchStatus.FAILED.value,
                                    report.summary.to_dict(), report, _now())
        return report

    # ------------------------------------------------------------ 查询接口
    def review_items(self, batchId: str) -> list[dict[str, Any]]:
        """返回待复核清单。"""
        return self.db.list_review(batchId)

    def save_review(self, patch: Patch | dict[str, Any]) -> bool:
        """回填待复核项（人工修正字段）。"""
        p = patch if isinstance(patch, Patch) else Patch.from_dict(patch)
        reviews = self.db.list_review()
        match = next((r for r in reviews
                      if r["invoice_id"] == p.invoiceId and r["field_key"] == p.fieldKey
                      and r["status"] == "OPEN"), None)
        if match is None:
            return False
        self.db.resolve_review(int(match["id"]), p.value, p.value)
        self.db.add_audit(AuditEvent(
            actor=p.operator or "operator", role=self._role,
            action=AuditAction.MANUAL_FIX, object=p.invoiceId, result="OK",
            detail=f"{p.fieldKey}={p.value}",
        ))
        return True

    def rollback_batch(self, batchId: str) -> bool:
        """回滚批次新建副本（仅副本，原件永不删）。"""
        removed = self.archiver.rollback()
        self.db.update_batch_status(batchId, BatchStatus.FAILED.value)
        self.db.add_audit(AuditEvent(
            actor="operator", role=self._role, action=AuditAction.ROLLBACK,
            object=batchId, result="OK", detail=f"删除副本 {len(removed)} 个",
        ))
        return True


__all__ = ["OrganizeEngine"]
