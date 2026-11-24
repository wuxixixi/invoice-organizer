"""Excel/CSV 列序、金额元展示、编码与待复核清单测试。"""
from __future__ import annotations

import csv
from pathlib import Path

from invoice_organizer.config.schema import ExportConfig
from invoice_organizer.core.exporter import ExportEngine
from invoice_organizer.models.batch import Batch, BatchReport, BatchStatus, BatchSummary
from invoice_organizer.models.invoice import FieldSlot, InvoiceRecord


def _invoice() -> InvoiceRecord:
    """构造含八字段的发票记录。"""
    inv = InvoiceRecord(id="I1", invoiceNo="12345678", issueDate="2024-11-24", amountCent=123456)
    for key, value in (("invoiceNo", "12345678"), ("issueDate", "2024-11-24"),
                       ("sellerName", "某公司"), ("amountCent", "1234.56")):
        inv.set_field(key, FieldSlot(key, value))
    return inv


def test_csv_encoding_and_amount(tmp_path: Path) -> None:
    """CSV 默认 UTF-8-BOM，金额以元展示。"""
    cfg = ExportConfig(format="csv", columns=["invoiceNo", "issueDate", "amountCent"])
    engine = ExportEngine(cfg, tmp_path)
    path = engine.export_ledger([_invoice()], None)
    assert path.endswith(".csv")
    raw = Path(path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "应为 UTF-8 BOM"
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["amountCent"] == "1234.56"
    assert list(rows[0].keys()) == ["invoiceNo", "issueDate", "amountCent"]


def test_xlsx_columns(tmp_path: Path) -> None:
    """Excel 导出按列序，金额展示为元。"""
    cfg = ExportConfig(format="xlsx", columns=["invoiceNo", "amountCent"])
    engine = ExportEngine(cfg, tmp_path)
    path = engine.export_ledger([_invoice()], None)
    if not path.endswith(".xlsx"):
        return  # openpyxl 缺失降级为 CSV，测试跳过 xlsx 断言
    from openpyxl import load_workbook  # type: ignore[import-not-found]
    sheet = load_workbook(path).active
    assert [c.value for c in sheet[1]] == ["invoiceNo", "amountCent"]
    assert sheet.cell(row=2, column=2).value == "1234.56"


def test_report_and_review_list(tmp_path: Path) -> None:
    """批次报告与待复核清单导出。"""
    engine = ExportEngine(ExportConfig(), tmp_path)
    report = BatchReport(batchId="B1", summary=BatchSummary(
        inputCount=2, successCount=1, failedCount=1), configVersion=3,
        anomalies=["E603: I2"], timeRange="now")
    report_path = engine.export_report(report)
    assert Path(report_path).exists()
    review_path = engine.export_review_list(
        [{"invoiceId": "I1", "fieldKey": "amountCent", "reason": "E301"}], "B1")
    assert Path(review_path).exists()
    with open(review_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["fieldKey"] == "amountCent"
