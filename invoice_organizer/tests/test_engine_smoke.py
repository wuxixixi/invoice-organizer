"""端到端冒烟：投放→计划→确认→归档→导出（无 OCR 降级路径）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from invoice_organizer.config.loader import ProfileLoader
from invoice_organizer.core.engine import OrganizeEngine
from invoice_organizer.core.watcher import StableFile
from invoice_organizer.models.plan import Patch
from invoice_organizer.parsers.base import ParseResult, RawTextField
from invoice_organizer.storage.db import Database
from invoice_organizer.utils.hashutil import HashUtil


@pytest.fixture
def engine(tmp_path: Path, config) -> OrganizeEngine:
    """构造端到端引擎（不启动真实监控线程，直接注入写稳文件）。"""
    db = Database(tmp_path / "test.db")
    db.init_schema()
    loader = ProfileLoader(tmp_path / "data")
    return OrganizeEngine(
        config=config, db=db, loader=loader, data_dir=tmp_path / "data",
        allow_network=False,  # start() 重载磁盘配置后也不得真实访问内网 LLM
    )


def _pdf(tmp_watch: Path) -> Path:
    """构造一个包含 %PDF 魔数与样例文本的伪 PDF（文本层适配器会尝试解析）。"""
    path = tmp_watch / "invoice.pdf"
    path.write_bytes(b"%PDF-1.4\nfake for smoke\n")
    return path


def test_full_flow_with_mocked_parser(engine: OrganizeEngine, tmp_watch: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """完整流程：写稳文件→解析(桩)→提取→计划→确认→归档→导出。"""
    path = _pdf(tmp_watch)
    sha = HashUtil.file_sha256(path)
    sample = ParseResult(
        fields=[
            RawTextField("", "发票号码：24312000000012345678", 1, (1, 2, 3, 4)),
            RawTextField("", "开票日期：2024年11月24日", 1, (1, 2, 3, 4)),
            RawTextField("", "购买方 名称：某某大学", 1, (1, 2, 3, 4)),
            RawTextField("", "纳税人识别号：123456789012345678", 1, (1, 2, 3, 4)),
            RawTextField("", "销售方 名称：某某科技有限公司", 1, (1, 2, 3, 4)),
            RawTextField("", "纳税人识别号：91310115MA1K3XXXXX", 1, (1, 2, 3, 4)),
            RawTextField("", "*技术服务*测试项目", 1, (1, 2, 3, 4)),
            RawTextField("", "合计 ¥1,234.56", 1, (1, 2, 3, 4)),
        ],
        sourceHash=sha, sourcePath=str(path), pageCount=1, adapterName="pdf_text_stub",
    )
    monkeypatch.setattr(engine.registry, "parse", lambda p, ctx: sample)

    batch_id = engine.start("default")
    stable = StableFile(path=str(path), sha256=sha, size=path.stat().st_size,
                        mtimeNs=path.stat().st_mtime_ns)
    code = engine.on_stable_file(stable)
    assert code == "E000"

    plan = engine.build_plan(batch_id)
    assert len(plan.items) == 1
    confirmed = engine.confirm_plan(plan.id, [Patch(
        invoiceId=plan.items[0].invoiceId, fieldKey="sellerName", value="已核验公司"
    )])
    assert confirmed is True

    report = engine.execute_batch(batch_id)
    assert report.summary.inputCount == 1
    assert report.summary.successCount == 1
    assert report.summary.failedCount == 0
    assert path.exists(), "原件必须只读保留"
    assert len(list((engine.data_dir / "exports").glob("ledger_*.csv"))) >= 1 or \
        len(list((engine.data_dir / "exports").glob("ledger_*.xlsx"))) >= 1


def test_unconfirmed_plan_blocked(engine: OrganizeEngine, tmp_watch: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """previewRequired=true 时，未确认计划禁止执行（E702）。"""
    path = _pdf(tmp_watch)
    sha = HashUtil.file_sha256(path)
    sample = ParseResult(
        fields=[RawTextField("", "发票号码：87654321", 1, (0, 0, 0, 0)),
                RawTextField("", "合计 ¥100.00", 1, (0, 0, 0, 0))],
        sourceHash=sha, sourcePath=str(path), adapterName="stub",
    )
    monkeypatch.setattr(engine.registry, "parse", lambda p, ctx: sample)
    batch_id = engine.start("default")
    stable = StableFile(str(path), sha, path.stat().st_size, path.stat().st_mtime_ns)
    engine.on_stable_file(stable)
    engine.build_plan(batch_id)
    report = engine.execute_batch(batch_id)
    assert report.summary.failedCount == 0
    assert report.summary.successCount == 0
    assert any("E702" in a for a in report.anomalies)
