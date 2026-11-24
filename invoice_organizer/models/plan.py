"""计划模型：计划状态、人工修正、冲突、计划项、计划。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PlanStatus(str, Enum):
    """计划状态机。"""

    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Patch:
    """人工修正：把某发票某字段改为指定值。"""

    invoiceId: str = ""
    fieldKey: str = ""
    value: str = ""
    operator: str = "operator"

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "invoiceId": self.invoiceId,
            "fieldKey": self.fieldKey,
            "value": self.value,
            "operator": self.operator,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Patch":
        """从字典恢复 Patch。"""
        return cls(
            invoiceId=str(data.get("invoiceId", "")),
            fieldKey=str(data.get("fieldKey", "")),
            value=str(data.get("value", "") or ""),
            operator=str(data.get("operator", "operator")),
        )


@dataclass
class Conflict:
    """冲突条目：同号异哈希等，只提示不自动取舍。"""

    id: str = ""
    type: str = ""
    invoiceNo: str = ""
    detail: str = ""
    status: str = "OPEN"
    resolution: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "id": self.id, "type": self.type, "invoiceNo": self.invoiceNo,
            "detail": self.detail, "status": self.status, "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conflict":
        """从字典恢复 Conflict。"""
        return cls(
            id=str(data.get("id", "")), type=str(data.get("type", "")),
            invoiceNo=str(data.get("invoiceNo", "")), detail=str(data.get("detail", "")),
            status=str(data.get("status", "OPEN")), resolution=str(data.get("resolution", "")),
        )


@dataclass
class PlanItem:
    """计划项：一条旧→新路径候选及其命中规则/缺字段/冲突/确认标志。"""

    invoiceId: str = ""
    oldPath: str = ""
    newPath: str = ""
    hitRuleId: str | None = None
    categoryLabel: str = ""
    missingFields: list[str] = field(default_factory=list)
    conflict: Conflict | None = None
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "invoiceId": self.invoiceId, "oldPath": self.oldPath, "newPath": self.newPath,
            "hitRuleId": self.hitRuleId, "categoryLabel": self.categoryLabel,
            "missingFields": list(self.missingFields),
            "conflict": self.conflict.to_dict() if self.conflict else None,
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanItem":
        """从字典恢复 PlanItem。"""
        conflict_raw = data.get("conflict")
        return cls(
            invoiceId=str(data.get("invoiceId", "")),
            oldPath=str(data.get("oldPath", "") or ""),
            newPath=str(data.get("newPath", "") or ""),
            hitRuleId=data.get("hitRuleId"),
            categoryLabel=str(data.get("categoryLabel", "")),
            missingFields=[str(v) for v in (data.get("missingFields") or [])],
            conflict=Conflict.from_dict(conflict_raw) if conflict_raw else None,
            confirmed=bool(data.get("confirmed", False)),
        )


def _utc_now() -> str:
    """返回 ISO-8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Plan:
    """一批次的整理计划（含若干 PlanItem）。"""

    id: str = ""
    batchId: str = ""
    items: list[PlanItem] = field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    profileVersion: int = 1
    createdAt: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "id": self.id, "batchId": self.batchId,
            "items": [i.to_dict() for i in self.items],
            "status": self.status.value, "profileVersion": self.profileVersion,
            "createdAt": self.createdAt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        """从字典恢复 Plan。"""
        return cls(
            id=str(data.get("id", "")),
            batchId=str(data.get("batchId", "")),
            items=[PlanItem.from_dict(d) for d in (data.get("items") or [])],
            status=PlanStatus(data.get("status", PlanStatus.DRAFT.value)),
            profileVersion=int(data.get("profileVersion", 1) or 1),
            createdAt=str(data.get("createdAt", _utc_now())),
        )


__all__ = ["PlanStatus", "Patch", "Conflict", "PlanItem", "Plan"]
