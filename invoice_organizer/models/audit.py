"""审计模型：角色、审计动作、审计事件（append-only）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Role(str, Enum):
    """本地演示级角色（不等同网络隔离，见 ARCHITECTURE 7.7）。"""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    READER = "READER"


class AuditAction(str, Enum):
    """关键审计动作。"""

    CONFIG_CHANGE = "CONFIG_CHANGE"
    CONFIRM = "CONFIRM"
    EXECUTE = "EXECUTE"
    EXPORT = "EXPORT"
    ROLLBACK = "ROLLBACK"
    MANUAL_FIX = "MANUAL_FIX"


def _utc_now() -> str:
    """返回 ISO-8601 UTC 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AuditEvent:
    """一条不可删除的审计事件。"""

    id: str = ""
    ts: str = field(default_factory=_utc_now)
    actor: str = "operator"
    role: Role = Role.OPERATOR
    action: AuditAction = AuditAction.EXECUTE
    object: str = ""
    result: str = "OK"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "id": self.id, "ts": self.ts, "actor": self.actor, "role": self.role.value,
            "action": self.action.value, "object": self.object,
            "result": self.result, "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEvent":
        """从字典恢复。"""
        return cls(
            id=str(data.get("id", "")),
            ts=str(data.get("ts", _utc_now())),
            actor=str(data.get("actor", "operator")),
            role=Role(data.get("role", Role.OPERATOR.value)),
            action=AuditAction(data.get("action", AuditAction.EXECUTE.value)),
            object=str(data.get("object", "") or ""),
            result=str(data.get("result", "OK")),
            detail=str(data.get("detail", "") or ""),
        )


__all__ = ["Role", "AuditAction", "AuditEvent"]
