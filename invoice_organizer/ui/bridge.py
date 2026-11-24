"""JsBridge：JS↔Python API，统一 {code,data,message} 响应，权限校验、异常转错误码。

- 所有方法返回 ``dict{code:int, data:Any, message:str}``，code=0 成功（E000）；
- 失败时 code 为错误码数字部分（如 E501 → 501），message 含完整错误码；
- 关键操作校验本地角色权限（ADMIN/OPERATOR/READER）；
- ``_wrap`` 捕获所有异常并映射错误码，绝不把堆栈暴露给前端。
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from invoice_organizer import ERROR_CODES
from invoice_organizer.config.schema import ProfileConfig
from invoice_organizer.core.engine import OrganizeEngine
from invoice_organizer.models.audit import AuditAction, AuditEvent, Role
from invoice_organizer.models.plan import Patch


def ok(data: Any = None, message: str = "E000") -> dict[str, Any]:
    """构造成功响应。"""
    return {"code": 0, "data": data, "message": message}


def fail(error_code: str, detail: str = "") -> dict[str, Any]:
    """构造失败响应，code 为错误码数字部分。"""
    numeric = int(error_code[1:]) if error_code.startswith("E") and error_code[1:].isdigit() else 1
    message = f"{error_code} {ERROR_CODES.get(error_code, '')}"
    if detail:
        message += f": {detail}"
    return {"code": numeric, "data": None, "message": message}


def require_roles(*roles: Role) -> Callable:
    """构造角色权限校验装饰器（模块级，避免类体内 self 绑定问题）。"""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(self: "JsBridge", *args: Any, **kwargs: Any) -> dict[str, Any]:
            if self.role not in roles:
                return fail("E701", f"当前角色 {self.role.value} 无权执行该操作")
            return fn(self, *args, **kwargs)
        return wrapper
    return decorator


class JsBridge:
    """前端桥接层。"""

    def __init__(self, engine: OrganizeEngine, role: Role = Role.ADMIN) -> None:
        """初始化。

        Args:
            engine: 编排引擎。
            role: 当前本地角色（演示级角色切换）。
        """
        self.engine = engine
        self.role = role

    # ---------------------------------------------------------------- 工具
    @staticmethod
    def _wrap(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """捕获异常并映射错误码。"""
        try:
            return fn()
        except PermissionError as exc:
            return fail("E701", str(exc))
        except ValueError as exc:
            message = str(exc)
            code = "E701"
            for candidate in ERROR_CODES:
                if message.startswith(candidate):
                    code = candidate
                    break
            return fail(code, message)
        except Exception as exc:  # noqa: BLE001 - 不向前端暴露堆栈
            return fail("E701", f"{type(exc).__name__}: {exc}")

    def set_role(self, role: str) -> dict[str, Any]:
        """切换本地角色（演示级）。"""
        try:
            self.role = Role(str(role).upper())
        except ValueError:
            return fail("E701", f"未知角色：{role}")
        self.engine._role = self.role
        return ok({"role": self.role.value})

    # ------------------------------------------------------------ 监控/计划
    def start_watching(self, profileId: str = "") -> dict[str, Any]:
        """启动监控。"""
        def call() -> dict[str, Any]:
            batch_id = self.engine.start(profileId)
            return ok({"batchId": batch_id})
        return self._wrap(call)

    def get_plan(self, batchId: str) -> dict[str, Any]:
        """获取计划预览（不存在时自动构建）。"""
        def call() -> dict[str, Any]:
            plan = self.engine.db.find_plan_by_batch(batchId)
            if plan is None:
                plan = self.engine.build_plan(batchId)
            return ok(plan.to_dict())
        return self._wrap(call)

    @staticmethod
    def _as_patch(raw: Any) -> Patch:
        """把 dict 或 Patch 统一为 Patch。"""
        return raw if isinstance(raw, Patch) else Patch.from_dict(raw or {})

    @require_roles(Role.ADMIN, Role.OPERATOR)
    def confirm_plan(self, planId: str, patches: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """确认计划（校验角色 + previewRequired 闸门）。"""
        def call() -> dict[str, Any]:
            result = self.engine.confirm_plan(
                planId, [self._as_patch(p) for p in (patches or [])]
            )
            return ok({"confirmed": result}) if result else fail("E702", "计划不存在")
        return self._wrap(call)

    @require_roles(Role.ADMIN, Role.OPERATOR)
    def execute_batch(self, batchId: str) -> dict[str, Any]:
        """执行批次归档。"""
        def call() -> dict[str, Any]:
            report = self.engine.execute_batch(batchId)
            if report.summary.failedCount and not report.summary.successCount:
                return fail("E602", "; ".join(report.anomalies))
            return ok(report.to_dict())
        return self._wrap(call)

    def list_batches(self) -> dict[str, Any]:
        """列出批次。"""
        def call() -> dict[str, Any]:
            return ok([b.to_dict() for b in self.engine.db.list_batches()])
        return self._wrap(call)

    def get_review(self, batchId: str) -> dict[str, Any]:
        """获取待复核清单。"""
        def call() -> dict[str, Any]:
            return ok(self.engine.review_items(batchId))
        return self._wrap(call)

    @require_roles(Role.ADMIN, Role.OPERATOR)
    def save_review(self, patch: dict[str, Any]) -> dict[str, Any]:
        """回填待复核字段。"""
        def call() -> dict[str, Any]:
            saved = self.engine.save_review(self._as_patch(patch))
            return ok({"saved": saved}) if saved else fail("E301", "未找到待复核项")
        return self._wrap(call)

    @require_roles(Role.ADMIN, Role.OPERATOR)
    def export(self, batchId: str, format: str = "xlsx") -> dict[str, Any]:
        """导出台账（格式由配置决定，format 参数做覆盖提示）。"""
        def call() -> dict[str, Any]:
            batch = self.engine.db.get_batch(batchId)
            if batch is None:
                return fail("E701", "批次不存在")
            path = self.engine.exporter.export_ledger(
                self.engine.db.list_invoices(batchId), batch
            )
            return ok({"path": path, "requestedFormat": format})
        return self._wrap(call)

    # -------------------------------------------------------------- 配置
    def get_config(self, profileId: str = "default") -> dict[str, Any]:
        """读取配置。"""
        def call() -> dict[str, Any]:
            config = self.engine.loader.load(profileId) if self.engine.loader \
                else self.engine.config
            return ok(config.to_dict())
        return self._wrap(call)

    @require_roles(Role.ADMIN)
    def save_config(self, configJson: str) -> dict[str, Any]:
        """保存配置（校验不通过返回 E701/E502）。"""
        def call() -> dict[str, Any]:
            config = ProfileConfig.from_json(configJson)
            errors = config.validate()
            if errors:
                return fail(errors[0].split(":")[0], "; ".join(errors))
            version = self.engine.loader.save(config) if self.engine.loader else config.version
            self.db_save_profile(config, version)
            return ok({"version": version})
        return self._wrap(call)

    def db_save_profile(self, config: Any, version: int) -> None:
        """把配置版本记录到数据库（审计与回溯）。"""
        self.engine.db.save_profile(config.profileId, version, config.schemaVersion,
                                    config.to_json(), True)
        self.engine.db.add_audit(AuditEvent(
            actor="admin", role=Role.ADMIN, action=AuditAction.CONFIG_CHANGE,
            object=config.profileId, result="OK", detail=f"version={version}",
        ))


__all__ = ["JsBridge", "ok", "fail"]
