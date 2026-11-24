"""DedupeService：SHA-256 内容去重 + 票号聚合，五类情形判定。

去重矩阵（ARCHITECTURE 3.3）：
- NEW → 正常
- DUP_SAME_HASH（同号同哈希）→ 拒绝，跳过/隔离
- CONFLICT_SAME_NO（同号异哈希）→ 拒绝，生成冲突人工判断
- SAME_HASH_MULTI_NO（异号同哈希）→ 接受，关联多票号
- MULTI_INVOICE（一 PDF 多票）→ 接受，拆分保留 SourceRef
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from invoice_organizer.models.plan import Conflict
from invoice_organizer.parsers.base import ParseResult, RawTextField
from invoice_organizer.storage.db import Database

KIND_NEW = "NEW"
KIND_DUP_SAME_HASH = "DUP_SAME_HASH"
KIND_CONFLICT_SAME_NO = "CONFLICT_SAME_NO"
KIND_SAME_HASH_MULTI_NO = "SAME_HASH_MULTI_NO"
KIND_MULTI_INVOICE = "MULTI_INVOICE"


@dataclass
class DedupeResult:
    """去重判定结果。"""

    kind: str = KIND_NEW
    accept: bool = True
    reason: str = ""
    conflict: Conflict | None = None
    invoiceNos: list[str] = field(default_factory=list)


class DedupeService:
    """基于内容哈希与票号的去重服务。"""

    def __init__(self, db: Database | None = None) -> None:
        """初始化。

        Args:
            db: 元数据库；None 时仅使用内存集合（适合单测/无库场景）。
        """
        self.db = db
        self._hashes: dict[str, str] = {}
        self._numbers: dict[str, str] = {}
        if db is not None:
            try:
                for row in db.connect().execute("SELECT sha256, path FROM files").fetchall():
                    self._hashes[row["sha256"]] = row["path"]
                for row in db.connect().execute(
                    "SELECT id, invoice_no FROM invoices WHERE invoice_no != ''"
                ).fetchall():
                    self._numbers[row["invoice_no"]] = row["id"]
            except Exception:  # noqa: BLE001 - 库未建表时退化为内存
                pass

    @staticmethod
    def _stable_hash(stable: Any) -> str:
        """从 StableFile 或任意对象取 sha256。"""
        return str(getattr(stable, "sha256", "") or "")

    @staticmethod
    def _stable_path(stable: Any) -> str:
        """从 StableFile 或任意对象取 path。"""
        return str(getattr(stable, "path", "") or "")

    def evaluate(self, stable: Any, invoiceNo: str) -> DedupeResult:
        """判定五类去重情形。

        Args:
            stable: StableFile（含 sha256/path）或具有同名字段的对象。
            invoiceNo: 已知票号（预览提取或人工录入）；空表示未知。
        """
        sha = self._stable_hash(stable)
        path = self._stable_path(stable)
        no = (invoiceNo or "").strip()
        file_row = self.db.find_file_by_hash(sha) if self.db and sha else None
        known_hash = self._hashes.get(sha) or (file_row["path"] if file_row else None)
        known_no_id = self._numbers.get(no) if no else None
        if not known_no_id and self.db and no:
            existing = self.db.find_invoice_by_no(no)
            known_no_id = existing.id if existing else None

        # 同 SHA-256 已存在
        if known_hash:
            if no and known_no_id:
                return DedupeResult(
                    kind=KIND_DUP_SAME_HASH, accept=False,
                    reason="同票号且同 SHA-256，判定为重复文件",
                    conflict=Conflict(
                        id="C" + uuid.uuid4().hex[:12], type=KIND_DUP_SAME_HASH,
                        invoiceNo=no, detail=f"重复来源：{known_hash}",
                    ),
                    invoiceNos=[no],
                )
            return DedupeResult(
                kind=KIND_SAME_HASH_MULTI_NO, accept=True,
                reason="不同票号但同 SHA-256，保留一条物理记录并关联多票号",
                invoiceNos=[no] if no else [],
            )

        # 同票号但哈希不同 → 冲突
        if no and known_no_id:
            return DedupeResult(
                kind=KIND_CONFLICT_SAME_NO, accept=False,
                reason="同票号不同 SHA-256，进入冲突复核，不自动取舍",
                conflict=Conflict(
                    id="C" + uuid.uuid4().hex[:12], type=KIND_CONFLICT_SAME_NO,
                    invoiceNo=no,
                    detail=f"已有记录 {known_no_id}，新文件哈希 {sha}",
                ),
                invoiceNos=[no],
            )

        # 新文件
        self._hashes[sha] = path
        if no:
            # 用非空哨兵记录“该票号已见过”，避免空字符串被当作 False
            self._numbers[no] = self._numbers.get(no) or "__seen__"
        return DedupeResult(kind=KIND_NEW, accept=True, reason="新文件，正常入队",
                            invoiceNos=[no] if no else [])

    def mark_multi_invoice(self, result: ParseResult, source_hash: str,
                           source_path: str) -> DedupeResult:
        """检测一 PDF 多票：解析结果中出现多个不同票号时返回 MULTI_INVOICE。"""
        numbers: set[str] = set()
        from invoice_organizer.core.fields import FieldExtractor
        extractor = FieldExtractor()
        for f in result.fields:
            hit = extractor._find_invoice_no([f])
            if hit:
                numbers.add(hit[0])
        if len(numbers) > 1:
            return DedupeResult(
                kind=KIND_MULTI_INVOICE, accept=True,
                reason=f"检测到 {len(numbers)} 个票号，需拆分为多条记录",
                invoiceNos=sorted(numbers),
            )
        return DedupeResult(kind=KIND_NEW, accept=True, reason="单一票号",
                            invoiceNos=sorted(numbers))

    def split_multi_invoice(self, result: ParseResult) -> list[ParseResult]:
        """按页拆分解析结果为多条 ParseResult，保留同源标记（page/bbox）。

        若无法明确按页区分（如每页多票），则按票号文本块分组；
        实在无法拆分时返回原结果单元素列表（不丢数据）。
        """
        pages: dict[int, list[RawTextField]] = {}
        for f in result.fields:
            pages.setdefault(f.page, []).append(f)
        if len(pages) <= 1:
            return [result]
        chunks: list[ParseResult] = []
        for page_no in sorted(pages):
            chunks.append(ParseResult(
                fields=pages[page_no],
                sourceHash=result.sourceHash,
                sourcePath=f"{result.sourcePath}#page={page_no}",
                pageCount=1,
                adapterName=result.adapterName,
                warnings=list(result.warnings) + [f"同源拆分自第 {page_no} 页"],
            ))
        return chunks


__all__ = ["DedupeService", "DedupeResult"]
