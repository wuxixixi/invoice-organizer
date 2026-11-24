"""批次模型：批次状态、统计、批次报告、批次。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class BatchStatus(str, Enum):
    """批次状态机（见 ARCHITECTURE 3.1）。"""

    RUNNING = "RUNNING"
    PREVIEW = "PREVIEW"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


@dataclass
class BatchSummary:
    """批次统计计数。"""

    inputCount: int = 0
    successCount: int = 0
    failedCount: int = 0
    conflictCount: int = 0
    reviewCount: int = 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "inputCount": self.inputCount, "successCount": self.successCount,
            "failedCount": self.failedCount, "conflictCount": self.conflictCount,
            "reviewCount": self.reviewCount,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BatchSummary":
        """从字典恢复。"""
        data = data or {}
        return cls(
            inputCount=int(data.get("inputCount", 0) or 0),
            successCount=int(data.get("successCount", 0) or 0),
            failedCount=int(data.get("failedCount", 0) or 0),
            conflictCount=int(data.get("conflictCount", 0) or 0),
            reviewCount=int(data.get("reviewCount", 0) or 0),
        )


@dataclass
class BatchReport:
    """批次报告：汇总 + 配置版本 + 异常摘要 + 时间范围。"""

    batchId: str = ""
    summary: BatchSummary = field(default_factory=BatchSummary)
    configVersion: int = 1
    anomalies: list[str] = field(default_factory=list)
    timeRange: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "batchId": self.batchId,
            "summary": self.summary.to_dict(),
            "configVersion": self.configVersion,
            "anomalies": list(self.anomalies),
            "timeRange": self.timeRange,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchReport":
        """从字典恢复。"""
        return cls(
            batchId=str(data.get("batchId", "")),
            summary=BatchSummary.from_dict(data.get("summary")),
            configVersion=int(data.get("configVersion", 1) or 1),
            anomalies=[str(v) for v in (data.get("anomalies") or [])],
            timeRange=str(data.get("timeRange", "")),
        )


def _utc_now() -> str:
    """返回 ISO-8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Batch:
    """批次：绑定配置快照，记录起止时间与报告。"""

    id: str = ""
    profileVersion: int = 1
    configSnapshot: str = ""
    status: BatchStatus = BatchStatus.RUNNING
    summary: BatchSummary = field(default_factory=BatchSummary)
    startedAt: str = field(default_factory=_utc_now)
    endedAt: str = ""
    report: BatchReport | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "id": self.id, "profileVersion": self.profileVersion,
            "configSnapshot": self.configSnapshot, "status": self.status.value,
            "summary": self.summary.to_dict(), "startedAt": self.startedAt,
            "endedAt": self.endedAt,
            "report": self.report.to_dict() if self.report else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Batch":
        """从字典恢复。"""
        report_raw = data.get("report")
        return cls(
            id=str(data.get("id", "")),
            profileVersion=int(data.get("profileVersion", 1) or 1),
            configSnapshot=str(data.get("configSnapshot", "") or ""),
            status=BatchStatus(data.get("status", BatchStatus.RUNNING.value)),
            summary=BatchSummary.from_dict(data.get("summary")),
            startedAt=str(data.get("startedAt", _utc_now())),
            endedAt=str(data.get("endedAt", "") or ""),
            report=BatchReport.from_dict(report_raw) if report_raw else None,
        )


__all__ = ["BatchStatus", "BatchSummary", "BatchReport", "Batch"]
