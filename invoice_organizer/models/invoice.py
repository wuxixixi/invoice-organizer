"""发票领域模型：置信、证据、字段槽位、行项目、来源引用、发票记录。

字段键与置信取值来自 ``invoice_organizer`` 全局常量（ARCHITECTURE 7.2）。
金额内部一律整数分（ARCHITECTURE 7.3）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from invoice_organizer import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_NEED_HUMAN,
    FIELD_KEYS,
    UNCATEGORIZED_LABEL,
)
from invoice_organizer.utils.money import MoneyUtil


class Confidence(str, Enum):
    """字段置信状态。继承 str 以便 JSON 序列化。"""

    HIGH = CONFIDENCE_HIGH
    LOW = CONFIDENCE_LOW
    NEED_HUMAN = CONFIDENCE_NEED_HUMAN


@dataclass
class Evidence:
    """字段证据：来源文件、页码（从 1 开始）、边界框、原文片段。"""

    sourceFile: str = ""
    page: int = 1
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    rawText: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典（bbox 转列表）。"""
        return {
            "sourceFile": self.sourceFile,
            "page": self.page,
            "bbox": list(self.bbox),
            "rawText": self.rawText,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Evidence":
        """从字典恢复 Evidence。"""
        data = data or {}
        bbox = data.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        return cls(
            sourceFile=str(data.get("sourceFile", "")),
            page=int(data.get("page", 1) or 1),
            bbox=tuple(float(v) for v in bbox),  # type: ignore[arg-type]
            rawText=str(data.get("rawText", "")),
        )


@dataclass
class FieldSlot:
    """单个字段槽位：值 + 置信 + 证据 + 是否已人工核验。"""

    key: str = ""
    value: str = ""
    confidence: Confidence = Confidence.NEED_HUMAN
    evidence: Evidence = field(default_factory=Evidence)
    verified: bool = False

    def is_review_needed(self) -> bool:
        """低置信与待确认均需人工复核。"""
        return self.confidence in (Confidence.LOW, Confidence.NEED_HUMAN) and not self.verified

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence.value,
            "evidence": self.evidence.to_dict(),
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FieldSlot":
        """从字典恢复 FieldSlot。"""
        return cls(
            key=str(data.get("key", "")),
            value=str(data.get("value", "") or ""),
            confidence=Confidence(data.get("confidence", Confidence.NEED_HUMAN.value)),
            evidence=Evidence.from_dict(data.get("evidence")),
            verified=bool(data.get("verified", False)),
        )


@dataclass
class ItemLine:
    """发票行项目。quantity 按 ARCHITECTURE 类图为 int（出库数量取整）。"""

    name: str = ""
    spec: str = ""
    quantity: int = 1
    amountCent: int = 0


@dataclass
class SourceRef:
    """来源引用：内容哈希 + 原始路径 + 页码 + 区域。"""

    fileHash: str = ""
    filePath: str = ""
    page: int = 1
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "fileHash": self.fileHash,
            "filePath": self.filePath,
            "page": self.page,
            "bbox": list(self.bbox),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRef":
        """从字典恢复 SourceRef。"""
        bbox = data.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        return cls(
            fileHash=str(data.get("fileHash", "")),
            filePath=str(data.get("filePath", "")),
            page=int(data.get("page", 1) or 1),
            bbox=tuple(float(v) for v in bbox),  # type: ignore[arg-type]
        )


@dataclass
class InvoiceRecord:
    """一票一记录：八字段槽位 + 行项目 + 来源 + 分类 + 状态。"""

    id: str = ""
    invoiceNo: str = ""
    issueDate: str = ""
    fields: list[FieldSlot] = field(default_factory=list)
    items: list[ItemLine] = field(default_factory=list)
    amountCent: int = 0
    categoryLabel: str = UNCATEGORIZED_LABEL
    categoryRuleId: str | None = None
    sources: list[SourceRef] = field(default_factory=list)
    status: str = "NEW"

    def get_field(self, key: str) -> FieldSlot:
        """按键取字段槽位；不存在时返回一个 need_human 空槽位（不抛异常）。"""
        for slot in self.fields:
            if slot.key == key:
                return slot
        return FieldSlot(key=key, value="", confidence=Confidence.NEED_HUMAN)

    def set_field(self, key: str, slot: FieldSlot) -> None:
        """设置（覆盖或追加）字段槽位。"""
        for idx, existing in enumerate(self.fields):
            if existing.key == key:
                self.fields[idx] = slot
                return
        self.fields.append(slot)

    def merge_items(self, items: list[ItemLine]) -> None:
        """合并行项目列表到当前记录（不做金额相加，仅追加明细）。"""
        self.items.extend(items)

    def add_source(self, source: SourceRef) -> None:
        """追加来源引用，避免重复（按 fileHash+page+bbox 去重）。"""
        signature = (source.fileHash, source.page, tuple(source.bbox))
        for existing in self.sources:
            if (existing.fileHash, existing.page, tuple(existing.bbox)) == signature:
                return
        self.sources.append(source)

    def review_fields(self) -> list[FieldSlot]:
        """返回需要人工复核的字段槽位列表（按八字段顺序）。"""
        by_key = {slot.key: slot for slot in self.fields}
        result: list[FieldSlot] = []
        for key in FIELD_KEYS:
            slot = by_key.get(key)
            if slot is None or slot.is_review_needed():
                result.append(slot or FieldSlot(key=key, confidence=Confidence.NEED_HUMAN))
        return result

    def to_row(self) -> dict[str, Any]:
        """转换为导出/规则匹配用的一行字典。

        八字段值来自字段槽位（amountCent 使用记录上的整数分），
        金额以元展示写入 ``amountCent`` 展示列，同时保留 ``amountCentValue``（分）。
        """
        by_key = {slot.key: slot.value for slot in self.fields}
        row: dict[str, Any] = {key: by_key.get(key, "") for key in FIELD_KEYS}
        row["amountCent"] = MoneyUtil.cent_to_yuan(self.amountCent)
        row["amountCentValue"] = self.amountCent
        row["categoryLabel"] = self.categoryLabel
        row["categoryRuleId"] = self.categoryRuleId or ""
        row["status"] = self.status
        return row

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典（字段槽位/来源均转字典）。"""
        return {
            "id": self.id,
            "invoiceNo": self.invoiceNo,
            "issueDate": self.issueDate,
            "fields": [s.to_dict() for s in self.fields],
            "items": [
                {"name": i.name, "spec": i.spec, "quantity": i.quantity,
                 "amountCent": i.amountCent}
                for i in self.items
            ],
            "amountCent": self.amountCent,
            "categoryLabel": self.categoryLabel,
            "categoryRuleId": self.categoryRuleId,
            "sources": [s.to_dict() for s in self.sources],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InvoiceRecord":
        """从字典恢复 InvoiceRecord。"""
        inv = cls(
            id=str(data.get("id", "")),
            invoiceNo=str(data.get("invoiceNo", "") or ""),
            issueDate=str(data.get("issueDate", "") or ""),
            amountCent=int(data.get("amountCent", 0) or 0),
            categoryLabel=str(data.get("categoryLabel", UNCATEGORIZED_LABEL)),
            categoryRuleId=data.get("categoryRuleId"),
            status=str(data.get("status", "NEW")),
        )
        inv.fields = [FieldSlot.from_dict(d) for d in (data.get("fields") or [])]
        inv.items = [
            ItemLine(
                name=str(d.get("name", "")),
                spec=str(d.get("spec", "")),
                quantity=int(d.get("quantity", 1) or 1),
                amountCent=int(d.get("amountCent", 0) or 0),
            )
            for d in (data.get("items") or [])
        ]
        inv.sources = [SourceRef.from_dict(d) for d in (data.get("sources") or [])]
        return inv


__all__ = [
    "Confidence",
    "Evidence",
    "FieldSlot",
    "ItemLine",
    "SourceRef",
    "InvoiceRecord",
]
