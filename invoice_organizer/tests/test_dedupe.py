"""五类去重情形与冲突判定测试。"""
from __future__ import annotations

from dataclasses import dataclass

from invoice_organizer.core.dedupe import (
    KIND_CONFLICT_SAME_NO,
    KIND_DUP_SAME_HASH,
    KIND_NEW,
    KIND_SAME_HASH_MULTI_NO,
    DedupeService,
)
from invoice_organizer.parsers.base import ParseResult, RawTextField
from invoice_organizer.storage.db import Database


@dataclass
class FakeStable:
    """模拟 StableFile。"""

    path: str
    sha256: str
    size: int = 100
    mtimeNs: int = 1


def test_new_file() -> None:
    """新文件正常入队。"""
    result = DedupeService().evaluate(FakeStable("a.pdf", "a" * 64), "12345678")
    assert result.kind == KIND_NEW
    assert result.accept is True


def test_duplicate_same_hash_same_no() -> None:
    """同号同哈希 → DUP_SAME_HASH，拒绝。"""
    service = DedupeService()
    service.evaluate(FakeStable("a.pdf", "a" * 64), "12345678")
    result = service.evaluate(FakeStable("a_copy.pdf", "a" * 64), "12345678")
    assert result.kind == KIND_DUP_SAME_HASH
    assert result.accept is False
    assert result.conflict is not None


def test_conflict_same_no_diff_hash() -> None:
    """同号异哈希 → CONFLICT_SAME_NO，生成冲突。"""
    service = DedupeService()
    service.evaluate(FakeStable("a.pdf", "a" * 64), "12345678")
    result = service.evaluate(FakeStable("b.pdf", "b" * 64), "12345678")
    assert result.kind == KIND_CONFLICT_SAME_NO
    assert result.accept is False
    assert result.conflict is not None
    assert result.conflict.invoiceNo == "12345678"


def test_same_hash_multi_no() -> None:
    """异号同哈希 → SAME_HASH_MULTI_NO，保留一条。"""
    service = DedupeService()
    service.evaluate(FakeStable("a.pdf", "a" * 64), "11111111")
    result = service.evaluate(FakeStable("a.pdf", "a" * 64), "22222222")
    assert result.kind == KIND_SAME_HASH_MULTI_NO
    assert result.accept is True


def test_dedupe_with_database(mem_db: Database) -> None:
    """从数据库加载已有哈希/票号进行去重判定。"""
    mem_db.insert_file("c" * 64, "old.pdf", 100, 1)
    service = DedupeService(mem_db)
    result = service.evaluate(FakeStable("new.pdf", "c" * 64), "33333333")
    assert result.kind == KIND_SAME_HASH_MULTI_NO
    assert result.accept is True


def test_split_multi_invoice() -> None:
    """一 PDF 多页按页拆分，保留同源哈希与页信息。"""
    result = ParseResult(
        fields=[
            RawTextField("", "发票号码：11111111", 1, (0, 0, 0, 0)),
            RawTextField("", "发票号码：22222222", 2, (0, 0, 0, 0)),
        ],
        sourceHash="d" * 64, sourcePath="multi.pdf", pageCount=2, adapterName="pdf_text",
    )
    service = DedupeService()
    detection = service.mark_multi_invoice(result, "d" * 64, "multi.pdf")
    assert detection.kind == "MULTI_INVOICE"
    assert detection.accept is True
    chunks = service.split_multi_invoice(result)
    assert len(chunks) == 2
    assert all(c.sourceHash == "d" * 64 for c in chunks)
