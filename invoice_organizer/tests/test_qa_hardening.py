"""QA 独立补充用例：对高风险行为补齐真实断言。

覆盖：路径遍历/软链接/重名、归档原子写/哈希复核/失败回滚/原件只读、
去重冲突元数据、一 PDF 多票、字段置信与金额口径、规则排序、模板注入、
配置目录关系与版本绑定、JsBridge 角色闸门、前端离线资源。

由 QA（严过关）独立编写，不复用工程师自审结论。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from invoice_organizer import (
    AMOUNT_SOURCE_EXCL_TAX,
    CONFIDENCE_NEED_HUMAN,
    FIELD_KEYS,
)
from invoice_organizer.config.loader import ProfileLoader
from invoice_organizer.config.schema import CategoryRule, MatchRule, ProfileConfig
from invoice_organizer.core.archiver import Archiver
from invoice_organizer.core.dedupe import (
    KIND_CONFLICT_SAME_NO,
    KIND_DUP_SAME_HASH,
    DedupeService,
)
from invoice_organizer.core.engine import OrganizeEngine
from invoice_organizer.core.fields import FieldExtractor
from invoice_organizer.core.rules import RuleEngine
from invoice_organizer.core.template import TemplateEngine
from invoice_organizer.core.watcher import StableFile
from invoice_organizer.models.audit import AuditAction, Role
from invoice_organizer.models.invoice import Confidence, FieldSlot, InvoiceRecord
from invoice_organizer.models.plan import Plan, PlanItem, PlanStatus
from invoice_organizer.parsers.base import ParseContext, ParseResult, RawTextField
from invoice_organizer.parsers.image_ocr import ImageOcrAdapter, TesseractOcrAdapter
from invoice_organizer.storage.db import Database
from invoice_organizer.ui.bridge import JsBridge
from invoice_organizer.utils.hashutil import HashUtil
from invoice_organizer.utils.paths import PathGuard, PathViolation


@dataclass
class _Stable:
    """最小 StableFile 替身。"""

    path: str
    sha256: str
    size: int = 10
    mtimeNs: int = 1


def _plan(items: list[PlanItem]) -> Plan:
    """构造已确认计划。"""
    return Plan(id="PQA", batchId="BQA", items=items, status=PlanStatus.CONFIRMED)


def _valid_config(tmp_path: Path) -> ProfileConfig:
    """构造目录互不重叠的合法配置。"""
    cfg = ProfileConfig.default()
    cfg.watch.path = str(tmp_path / "inbox")
    cfg.output.archiveRoot = str(tmp_path / "archive")
    cfg.output.quarantine = str(tmp_path / "quarantine")
    return cfg


# --------------------------------------------------------------- 路径安全
@pytest.mark.parametrize("rel", [
    "../escape.pdf",
    "../../etc/passwd",
    "/etc/passwd",
    "C:/Windows/evil.pdf",
    r"\\server\share\evil.pdf",
])
def test_safe_join_rejects_traversal_and_absolute(rel: str, tmp_archive: Path) -> None:
    """路径守卫必须拒绝 ..、绝对路径、盘符与 UNC 注入。"""
    guard = PathGuard(str(tmp_archive))
    with pytest.raises(PathViolation) as exc:
        guard.safe_join(str(tmp_archive), rel)
    assert exc.value.error_code == "E501"


def test_resolve_inside_rejects_symlink_escape(tmp_path: Path, tmp_archive: Path) -> None:
    """归档根内指向外部的软链接必须拒绝（E501）。"""
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    link = tmp_archive / "link_out"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - 平台能力
        pytest.skip(f"当前环境不支持创建符号链接：{exc}")
    guard = PathGuard(str(tmp_archive))
    with pytest.raises(PathViolation):
        guard.resolve_inside(str(tmp_archive), str(link / "evil.pdf"))


def test_next_available_never_overwrites(tmp_archive: Path) -> None:
    """重名追加 _2/_3，原文件字节不变。"""
    target = tmp_archive / "a.pdf"
    target.write_bytes(b"original")
    second = Path(PathGuard.next_available(target))
    assert second == tmp_archive / "a_2.pdf"
    second.write_bytes(b"second")
    third = Path(PathGuard.next_available(target))
    assert third == tmp_archive / "a_3.pdf"
    assert target.read_bytes() == b"original"
    assert second.read_bytes() == b"second"


def test_archiver_rejects_traversal_and_continues(tmp_watch: Path, tmp_archive: Path,
                                                  tmp_path: Path) -> None:
    """归档执行器：越界项记 E501 且不落盘，后续安全项照常完成。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"%PDF-1.4 payload")
    outside = tmp_path / "outside_target.pdf"
    bad = PlanItem(invoiceId="Ibad", oldPath=str(src), newPath="../outside_target.pdf")
    good = PlanItem(invoiceId="Igood", oldPath=str(src), newPath="safe/ok.pdf")

    results = Archiver(PathGuard(str(tmp_archive))).execute(_plan([bad, good]))

    assert len(results) == 2
    assert results[0].errorCode == "E501"
    assert results[0].verified is False
    assert results[0].archivedPath == ""
    assert not outside.exists(), "越界路径绝不允许落盘"
    assert results[1].verified is True
    assert (tmp_archive / "safe" / "ok.pdf").exists()


def test_archiver_rejects_symlinked_parent(tmp_watch: Path, tmp_archive: Path,
                                           tmp_path: Path) -> None:
    """目标父目录为越界软链接时拒绝写入。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"data")
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    link = tmp_archive / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover
        pytest.skip(f"当前环境不支持创建符号链接：{exc}")
    item = PlanItem(invoiceId="Ilink", oldPath=str(src), newPath="linked/evil.pdf")

    results = Archiver(PathGuard(str(tmp_archive))).execute(_plan([item]))

    assert results[0].errorCode == "E501"
    assert not (outside / "evil.pdf").exists()


# --------------------------------------------------------------- 归档事务
def test_verify_detects_tampered_copy(tmp_path: Path) -> None:
    """写后哈希复核能识别单字节篡改。"""
    copy = tmp_path / "a.pdf"
    copy.write_bytes(b"abc")
    digest = HashUtil.file_sha256(copy)
    assert Archiver.verify(digest, str(copy)) is True
    copy.write_bytes(b"abd")
    assert Archiver.verify(digest, str(copy)) is False


def test_hash_mismatch_sets_e603(tmp_watch: Path, tmp_archive: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """复核失败必须报 E603 且 verified=False。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"%PDF content")
    item = PlanItem(invoiceId="Ihash", oldPath=str(src), newPath="hash/fail.pdf")
    monkeypatch.setattr(Archiver, "verify", staticmethod(lambda *_a, **_k: False))

    results = Archiver(PathGuard(str(tmp_archive))).execute(_plan([item]))

    assert results[0].errorCode == "E603"
    assert results[0].verified is False
    assert results[0].archivedPath, "失败项也需保留现场路径供日志/回滚"


def test_write_atomic_uses_same_dir_temp_then_replace(tmp_watch: Path, tmp_archive: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """原子写：临时文件与目标同目录，replace 后无临时残留。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"payload")
    dest = tmp_archive / "sub" / "a.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    original_replace = os.replace
    seen: dict[str, str] = {}

    def spy_replace(source: str, target: str) -> None:
        seen["source"] = str(source)
        seen["target"] = str(target)
        assert Path(source).exists(), "replace 前临时文件必须已写入"
        assert Path(source).parent == dest.parent, "临时文件必须同目录以保证原子性"
        assert Path(source).name.startswith(".a.pdf.") and Path(source).name.endswith(".tmp")
        assert not Path(target).exists(), "replace 前目标不应被提前创建"
        original_replace(source, target)

    monkeypatch.setattr("invoice_organizer.core.archiver.os.replace", spy_replace)

    archiver = Archiver(PathGuard(str(tmp_archive)))
    assert archiver._write_atomic(str(src), str(dest)) is True
    assert seen["target"] == str(dest)
    assert dest.read_bytes() == b"payload"
    assert list(dest.parent.glob(".*.tmp")) == []


def test_archive_keeps_source_bytes_and_mtime(tmp_watch: Path, tmp_archive: Path) -> None:
    """原件只读：字节与 mtime_ns 均不得被归档改变。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"%PDF-1.4 original")
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    item = PlanItem(invoiceId="Iro", oldPath=str(src), newPath="ro/copy.pdf")

    results = Archiver(PathGuard(str(tmp_archive))).execute(_plan([item]))

    assert results[0].verified is True
    assert src.exists()
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before


@pytest.fixture
def qa_engine(tmp_path: Path, config: ProfileConfig) -> OrganizeEngine:
    """构造不依赖 GUI/OCR 的引擎实例。"""
    db = Database(tmp_path / "qa.db")
    db.init_schema()
    return OrganizeEngine(
        config=config, db=db, loader=ProfileLoader(tmp_path / "data"),
        data_dir=tmp_path / "data",
    )


def test_engine_failure_rolls_back_copies_only(qa_engine: OrganizeEngine, tmp_watch: Path,
                                               tmp_archive: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """批次失败自动回滚本批副本，原件与字节保留。"""
    src = tmp_watch / "invoice.pdf"
    src.write_bytes(b"%PDF-1.4 rollback-case")
    sha = HashUtil.file_sha256(src)
    sample = ParseResult(
        fields=[RawTextField("", "发票号码：11111111", 1, (0, 0, 1, 1)),
                RawTextField("", "合计 ¥10.00", 1, (0, 0, 1, 1))],
        sourceHash=sha, sourcePath=str(src), adapterName="pdf_text_stub",
    )
    monkeypatch.setattr(qa_engine.registry, "parse", lambda *_a, **_k: sample)
    monkeypatch.setattr(Archiver, "verify", staticmethod(lambda *_a, **_k: False))

    batch_id = qa_engine.start("default")
    qa_engine.on_stable_file(StableFile(str(src), sha, src.stat().st_size,
                                        src.stat().st_mtime_ns))
    plan = qa_engine.build_plan(batch_id)
    assert qa_engine.confirm_plan(plan.id, []) is True
    report = qa_engine.execute_batch(batch_id)

    assert report.summary.failedCount == 1
    assert report.summary.successCount == 0
    assert any("E603" in anomaly for anomaly in report.anomalies)
    assert src.exists() and src.read_bytes() == b"%PDF-1.4 rollback-case"
    assert list(tmp_archive.rglob("*.pdf")) == [], "失败批次副本必须全部回滚"


# ----------------------------------------------------------------- 去重
def test_dedupe_conflict_metadata_is_actionable() -> None:
    """两类拒绝情形需带正确 conflict.type 与新哈希，供人工复核。"""
    service = DedupeService()
    service.evaluate(_Stable("a.pdf", "a" * 64), "12345678")
    dup = service.evaluate(_Stable("a_copy.pdf", "a" * 64), "12345678")
    conflict_same_no = service.evaluate(_Stable("b.pdf", "b" * 64), "12345678")

    assert dup.kind == KIND_DUP_SAME_HASH
    assert dup.conflict is not None and dup.conflict.type == KIND_DUP_SAME_HASH
    assert conflict_same_no.kind == KIND_CONFLICT_SAME_NO
    assert conflict_same_no.conflict is not None
    assert conflict_same_no.conflict.type == KIND_CONFLICT_SAME_NO
    assert "b" * 64 in conflict_same_no.conflict.detail


def test_one_pdf_two_invoices_produces_two_plan_items(qa_engine: OrganizeEngine,
                                                      tmp_watch: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """一 PDF 多票拆成两条记录，且保留同源哈希。"""
    src = tmp_watch / "multi.pdf"
    src.write_bytes(b"%PDF-1.4 multi")
    sha = HashUtil.file_sha256(src)
    parsed = ParseResult(
        fields=[
            RawTextField("", "发票号码：11111111", 1, (1, 2, 3, 4)),
            RawTextField("", "合计 ¥10.00", 1, (1, 2, 3, 4)),
            RawTextField("", "发票号码：22222222", 2, (1, 2, 3, 4)),
            RawTextField("", "合计 ¥20.00", 2, (1, 2, 3, 4)),
        ],
        sourceHash=sha, sourcePath=str(src), pageCount=2, adapterName="pdf_text_stub",
    )
    monkeypatch.setattr(qa_engine.registry, "parse", lambda *_a, **_k: parsed)

    batch_id = qa_engine.start("default")
    code = qa_engine.on_stable_file(StableFile(str(src), sha, src.stat().st_size,
                                               src.stat().st_mtime_ns))
    plan = qa_engine.build_plan(batch_id)

    assert code == "E000"
    assert len(plan.items) == 2
    invoices = qa_engine.db.list_invoices(batch_id)
    assert {inv.invoiceNo for inv in invoices} == {"11111111", "22222222"}
    assert all(any(ref.fileHash == sha for ref in inv.sources) for inv in invoices)


# --------------------------------------------------------------- 存储往返
def test_database_roundtrip_preserves_invoice_values() -> None:
    """落库再读出后，票号/日期/金额/字段槽位必须保持原值。"""
    inv = InvoiceRecord(id="Iroundtrip", invoiceNo="24312000000012345678",
                        issueDate="2024-11-24", amountCent=123456, status="PREVIEW")
    inv.set_field("invoiceNo", FieldSlot("invoiceNo", "24312000000012345678"))
    inv.set_field("amountCent", FieldSlot("amountCent", "1234.56"))
    db = Database(":memory:")
    db.init_schema()
    db.save_invoice(inv)

    listed = db.list_invoices()
    assert len(listed) == 1
    assert listed[0].invoiceNo == "24312000000012345678"
    assert listed[0].issueDate == "2024-11-24"
    assert listed[0].amountCent == 123456
    assert listed[0].get_field("invoiceNo").value == "24312000000012345678"

    fetched = db.get_invoice("Iroundtrip")
    assert fetched is not None and fetched.invoiceNo == "24312000000012345678"
    assert fetched.amountCent == 123456


def test_database_roundtrip_preserves_batch_snapshot() -> None:
    """批次配置快照与版本落库后必须能原样读回（回溯依据）。"""
    from invoice_organizer.models.batch import Batch, BatchStatus

    db = Database(":memory:")
    db.init_schema()
    snapshot = '{"version": 7, "profileId": "default"}'
    db.save_batch(Batch(id="B7", profileVersion=7, configSnapshot=snapshot,
                        status=BatchStatus.PREVIEW))

    fetched = db.get_batch("B7")
    assert fetched is not None
    assert fetched.profileVersion == 7
    assert fetched.configSnapshot == snapshot


def test_exported_ledger_contains_extracted_values(qa_engine: OrganizeEngine,
                                                   tmp_watch: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端导出的台账必须含票号与金额，而非空行。"""
    import csv

    src = tmp_watch / "invoice.pdf"
    src.write_bytes(b"%PDF-1.4 export-case")
    sha = HashUtil.file_sha256(src)
    parsed = ParseResult(
        fields=[RawTextField("", "发票号码：24312000000012345678", 1, (1, 2, 3, 4)),
                RawTextField("", "开票日期：2024年11月24日", 1, (1, 2, 3, 4)),
                RawTextField("", "合计 ¥1,234.56", 1, (1, 2, 3, 4))],
        sourceHash=sha, sourcePath=str(src), adapterName="pdf_text_stub",
    )
    monkeypatch.setattr(qa_engine.registry, "parse", lambda *_a, **_k: parsed)
    qa_engine.config.export.format = "csv"

    batch_id = qa_engine.start("default")
    qa_engine.on_stable_file(StableFile(str(src), sha, src.stat().st_size,
                                        src.stat().st_mtime_ns))
    plan = qa_engine.build_plan(batch_id)
    qa_engine.confirm_plan(plan.id, [])
    qa_engine.execute_batch(batch_id)

    ledgers = list((qa_engine.data_dir / "exports").glob("ledger_*.csv"))
    assert ledgers, "应生成 CSV 台账"
    with open(ledgers[0], encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows, "台账应至少有一行数据"
    assert rows[0].get("invoiceNo") == "24312000000012345678"
    assert rows[0].get("amountCent") == "1234.56"


# ----------------------------------------------------------------- 字段
def test_ocr_valid_tax_id_is_low_confidence() -> None:
    """OCR/模糊来源税号即使格式合法也只能是 low（ARCHITECTURE 7.2）。"""
    result = ParseResult(
        fields=[RawTextField("", "纳税人识别号：123456789012345678", 1, (0, 0, 1, 1))],
        sourceHash="a" * 64, sourcePath="scan.png", adapterName="image_ocr:tesseract",
    )
    inv = FieldExtractor().extract(result)
    assert inv.get_field("buyerTaxId").confidence == Confidence.LOW


def test_multiple_item_lines_merged_into_one_record() -> None:
    """多行项目合并为同一记录的多个 ItemLine，金额仍为整数分。"""
    result = ParseResult(fields=[
        RawTextField("", "发票号码：24312000000012345678", 1, (0, 0, 1, 1)),
        RawTextField("", "*信息技术服务*技术服务费", 1, (0, 0, 1, 1)),
        RawTextField("", "*办公用品*打印纸 A4", 1, (0, 0, 1, 1)),
        RawTextField("", "合计 ¥1,234.56", 1, (0, 0, 1, 1)),
    ], sourceHash="b" * 64, sourcePath="sample.pdf", adapterName="pdf_text")

    inv = FieldExtractor().extract(result, AMOUNT_SOURCE_EXCL_TAX)

    assert len(inv.items) == 2, "多行项目应合并到同一条发票记录"
    assert isinstance(inv.amountCent, int) and not isinstance(inv.amountCent, bool)
    assert inv.amountCent == 123456
    assert tuple(slot.key for slot in inv.fields) == FIELD_KEYS


def test_invalid_date_and_amount_are_need_human() -> None:
    """非法日期与不可识别金额留空并标 need_human，不臆造。"""
    result = ParseResult(fields=[
        RawTextField("", "开票日期：2024-13-45", 1, (0, 0, 1, 1)),
        RawTextField("", "合计 无法辨认", 1, (0, 0, 1, 1)),
    ], sourceHash="c" * 64, sourcePath="bad.pdf", adapterName="pdf_text")

    inv = FieldExtractor().extract(result)

    date_slot = inv.get_field("issueDate")
    amount_slot = inv.get_field("amountCent")
    assert date_slot.value == "" and date_slot.confidence.value == CONFIDENCE_NEED_HUMAN
    assert amount_slot.value == "" and amount_slot.confidence.value == CONFIDENCE_NEED_HUMAN
    assert inv.amountCent == 0


# ----------------------------------------------------------- 规则与模板
def test_rules_sorted_by_priority_even_if_config_unsorted() -> None:
    """配置顺序被打乱时仍按 priority 升序，首条命中即停。"""
    inv = InvoiceRecord(invoiceNo="1")
    inv.set_field("itemName", FieldSlot("itemName", "交通费"))
    rules = [
        CategoryRule("late", "晚命中", 20, MatchRule("itemName", contains="交通")),
        CategoryRule("early", "早命中", 10, MatchRule("itemName", contains="交通")),
    ]
    assert RuleEngine(rules).classify(inv) == ("早命中", "early")


def test_template_rendering_is_pure_text_and_sanitized() -> None:
    """模板不执行脚本/表达式；未知占位符剔除；路径注入被清洗。"""
    cfg = ProfileConfig.default()
    cfg.directoryTemplate = "{sellerName}/../{evil}"
    cfg.filenameTemplate = "{sellerName}_{evil}_{__import__('os')}"
    inv = InvoiceRecord(invoiceNo="12345678")
    inv.set_field("sellerName", FieldSlot(
        "sellerName", "<script>alert(1)</script>|../../etc/passwd"))
    engine = TemplateEngine(cfg)

    name = engine.render_name(inv, "B1")
    directory = engine.render_dir(inv, "B1")

    assert "<script>" not in name and "|" not in name
    assert "evil" not in name and "__import__" not in name
    assert ".." not in directory and not directory.startswith("/")
    assert any("evil" in warning for warning in engine.warnings)


def test_template_output_stays_inside_archive_root(tmp_archive: Path) -> None:
    """模板渲染结果交路径守卫后必须落在归档根内。"""
    cfg = ProfileConfig.default()
    cfg.directoryTemplate = "../../{issueDate}"
    cfg.filenameTemplate = "{issueDate}_{invoiceNo}"
    inv = InvoiceRecord(invoiceNo="12345678", issueDate="2024-11-24")
    engine = TemplateEngine(cfg)
    rel = f"{engine.render_dir(inv, 'B1')}/{engine.render_name(inv, 'B1')}"

    target = PathGuard(str(tmp_archive)).target_for(rel)

    assert Path(target).is_relative_to(Path(tmp_archive).resolve())
    assert ".." not in rel


# ----------------------------------------------------------------- 配置
@pytest.mark.parametrize(("watch", "archive", "quarantine"), [
    ("/qa/inbox", "/qa/inbox/archive", "/qa/quarantine"),
    ("/qa/inbox/archive", "/qa/inbox", "/qa/quarantine"),
    ("/qa/same", "/qa/same", "/qa/quarantine"),
    ("/qa/inbox", "/qa/archive", "/qa/archive/quarantine"),
])
def test_config_rejects_all_directory_overlaps(watch: str, archive: str,
                                               quarantine: str) -> None:
    """输入/归档/隔离目录任意互相包含均报 E502。"""
    cfg = ProfileConfig.default()
    cfg.watch.path = watch
    cfg.output.archiveRoot = archive
    cfg.output.quarantine = quarantine
    assert any(error.startswith("E502") for error in cfg.validate())


def test_config_rejects_bad_schema_priority_and_placeholder(tmp_path: Path) -> None:
    """版本不兼容、priority 重复、非法占位符均报 E701。"""
    cfg = _valid_config(tmp_path)
    cfg.schemaVersion = 999
    cfg.categories = [
        CategoryRule("a", "A", 1, MatchRule("itemName", contains="交通")),
        CategoryRule("b", "B", 1, MatchRule("itemName", contains="办公")),
    ]
    cfg.filenameTemplate = "{evil}"

    errors = cfg.validate()

    assert any("E701" in e and "schemaVersion" in e for e in errors)
    assert any("priority" in e for e in errors)
    assert any("非法占位符" in e for e in errors)


def test_loader_keeps_history_and_snapshot(tmp_path: Path) -> None:
    """配置保存版本递增，历史版本文件保留，快照绑定当时版本。"""
    loader = ProfileLoader(tmp_path / "data")
    cfg = loader.load("qa")
    assert cfg.version == 1

    assert loader.save(cfg) == 2
    assert loader.save(cfg) == 3
    profile_dir = loader.data_dir / "profiles" / "qa"
    assert all((profile_dir / f"v{v}.json").exists() for v in (1, 2, 3))

    v1 = ProfileConfig.from_json((profile_dir / "v1.json").read_text(encoding="utf-8"))
    assert '"version": 1' in loader.snapshot(v1)
    assert loader.load("qa").version == 3


def test_plan_version_matches_batch_snapshot(qa_engine: OrganizeEngine) -> None:
    """批次启动时绑定配置快照，计划版本不得随后续配置变更漂移。"""
    batch_id = qa_engine.start("default")
    batch = qa_engine.db.get_batch(batch_id)
    original_version = batch.profileVersion

    qa_engine.config.version = original_version + 7
    plan = qa_engine.build_plan(batch_id)

    assert plan.profileVersion == original_version, "计划必须绑定批次启动时的配置版本"
    assert f'"version": {original_version}' in batch.configSnapshot


# --------------------------------------------------------------- 权限闸门
class _StubDB:
    """仅记录调用，不访问真实存储。"""

    def __init__(self) -> None:
        self.profiles: list[tuple[Any, ...]] = []
        self.audits: list[Any] = []

    def save_profile(self, *args: Any, **_kwargs: Any) -> None:
        self.profiles.append(args)

    def add_audit(self, event: Any) -> None:
        self.audits.append(event)


class _StubLoader:
    """固定返回版本号的配置加载器替身。"""

    def __init__(self, cfg: ProfileConfig) -> None:
        self.cfg = cfg

    def save(self, _cfg: ProfileConfig) -> int:
        return 2

    def load(self, _profile_id: str) -> ProfileConfig:
        return self.cfg


class _StubEngine:
    """关键操作一旦被调用即失败，用于验证闸门确实拦截。"""

    def __init__(self, cfg: ProfileConfig) -> None:
        self.config = cfg
        self.db = _StubDB()
        self.loader = _StubLoader(cfg)
        self._role = Role.ADMIN

    def confirm_plan(self, *_a: Any, **_k: Any) -> bool:
        raise AssertionError("无权限时不应调用 confirm_plan")

    def execute_batch(self, *_a: Any, **_k: Any) -> None:
        raise AssertionError("无权限时不应调用 execute_batch")

    def save_review(self, *_a: Any, **_k: Any) -> bool:
        raise AssertionError("无权限时不应调用 save_review")


def test_bridge_config_change_requires_admin(tmp_path: Path) -> None:
    """OPERATOR 拒绝配置变更（E701/701），ADMIN 通过且记审计。"""
    cfg = _valid_config(tmp_path)
    stub = _StubEngine(cfg)
    bridge = JsBridge(stub, role=Role.OPERATOR)

    denied = bridge.save_config(cfg.to_json())
    assert denied["code"] == 701
    assert "E701" in denied["message"]
    assert stub.db.profiles == [] and stub.db.audits == []

    bridge.set_role("admin")
    allowed = bridge.save_config(cfg.to_json())
    assert allowed["code"] == 0
    assert allowed["data"]["version"] == 2
    assert len(stub.db.audits) == 1
    assert stub.db.audits[0].action == AuditAction.CONFIG_CHANGE


def test_bridge_reader_denied_all_mutating_operations(tmp_path: Path) -> None:
    """READER 只读：确认/执行/回填/导出均被闸门拒绝。"""
    stub = _StubEngine(_valid_config(tmp_path))
    bridge = JsBridge(stub, role=Role.READER)

    responses = [
        bridge.confirm_plan("P1"),
        bridge.execute_batch("B1"),
        bridge.save_review({"invoiceId": "I1", "fieldKey": "sellerName", "value": "x"}),
        bridge.export("B1"),
    ]

    assert all(response["code"] == 701 for response in responses)
    assert all("E701" in response["message"] for response in responses)
    assert bridge.get_config("default")["code"] == 0, "只读角色应能读取配置"


# ----------------------------------------------------------- OCR 降级
def test_ocr_timeout_retries_finite_then_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """OCR 超时有限重试，耗尽后降级并给出 E401/E402，不抛异常。"""
    adapter = ImageOcrAdapter(backend=TesseractOcrAdapter())
    monkeypatch.setattr(adapter, "parse_in_subprocess", lambda *_a, **_k: ([], "timeout"))
    ctx = ParseContext(fileHash="a" * 64, filePath="x.png", timeoutSec=1, retry=2)

    result = adapter.parse("x.png", ctx)

    assert result.fields == []
    assert sum(1 for warning in result.warnings if "E401" in warning) == 2
    assert any("E402" in warning for warning in result.warnings)


def test_frontend_has_no_external_resources() -> None:
    """前端三件套无外链脚本/样式/字体，符合离线要求。"""
    ui_dir = Path(__file__).resolve().parents[1] / "ui"
    for name in ("index.html", "app.js", "style.css"):
        text = (ui_dir / name).read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, f"{name} 含外链"
        assert not re.search(r"(?:src|href)\s*=\s*['\"]//", text), f"{name} 含协议相对外链"
