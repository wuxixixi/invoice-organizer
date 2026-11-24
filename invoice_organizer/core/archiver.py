"""Archiver + PathGuard 集成：事务化批次写盘、原子重命名、不覆盖重名、
写后 SHA-256 复核、失败回滚、原件只读。

关键约束（ARCHITECTURE 1.4 C7 / 7.6）：
- 原件只读永不删除；
- 副本先写临时文件（同目录）再 os.replace 原子重命名；
- 不覆盖重名（PathGuard.next_available 追加序号）；
- 写后 SHA-256 复核，不一致 → E603；
- 批次失败回滚本批新建副本（createdPaths），保留成功项与日志。
"""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from invoice_organizer import ERROR_CODES
from invoice_organizer.models.plan import Plan, PlanItem
from invoice_organizer.storage.db import Database
from invoice_organizer.utils.hashutil import HashUtil
from invoice_organizer.utils.logging import get_logger
from invoice_organizer.utils.paths import PathGuard, PathViolation


@dataclass
class ArchiveResult:
    """单条归档结果。"""

    planItemId: str = ""
    invoiceId: str = ""
    archivedPath: str = ""
    sha256: str = ""
    sourceSha256: str = ""
    verified: bool = False
    errorCode: str = ""

    def to_dict(self) -> dict[str, object]:
        """转换为字典。"""
        return {
            "planItemId": self.planItemId, "invoiceId": self.invoiceId,
            "archivedPath": self.archivedPath, "sha256": self.sha256,
            "sourceSha256": self.sourceSha256, "verified": self.verified,
            "errorCode": self.errorCode,
        }


class Archiver:
    """事务化归档执行器。"""

    def __init__(self, guard: PathGuard, db: Database | None = None) -> None:
        """初始化。

        Args:
            guard: 路径守卫（绑定归档根）。
            db: 元数据库（可选，用于持久化归档结果）。
        """
        self.guard = guard
        self.db = db
        self.createdPaths: list[str] = []
        self._logger = get_logger()

    # ---------------------------------------------------------------- 执行
    def execute(self, plan: Plan) -> list[ArchiveResult]:
        """执行批次归档。

        Args:
            plan: 已确认计划。

        Returns:
            每条计划项的归档结果列表。

        Raises:
            ValueError: 计划状态非 CONFIRMED（E702）。
        """
        if plan.status.value != "CONFIRMED":
            raise ValueError(f"E702 {ERROR_CODES['E702']}: 计划未确认禁止执行")
        self.createdPaths = []
        results: list[ArchiveResult] = []
        for item in plan.items:
            result = self._execute_one(item)
            results.append(result)
            if self.db is not None:
                self.db.save_archive(result)
        return results

    def _execute_one(self, item: PlanItem) -> ArchiveResult:
        """处理单条计划项：路径校验 → 重名 → 原子写 → 哈希复核。"""
        result = ArchiveResult(
            planItemId=f"PI{uuid.uuid4().hex[:12]}",
            invoiceId=item.invoiceId,
        )
        src = item.oldPath
        try:
            # 原件存在性与只读检查（绝不删除/修改原件）
            if not src or not Path(src).is_file():
                result.errorCode = ERROR_CODES["E102"]
                return result
            if Path(src).is_symlink():
                result.errorCode = ERROR_CODES["E501"]
                return result
            result.sourceSha256 = HashUtil.file_sha256(src)

            # 路径安全复核（相对归档根）
            rel = item.newPath.replace("\\", "/").lstrip("/")
            try:
                dest_str = self.guard.resolve_inside(self.guard.archiveRoot,
                                                     os.path.join(self.guard.archiveRoot, rel))
            except PathViolation:
                result.errorCode = ERROR_CODES["E501"]
                return result

            # 不覆盖重名
            dest_str = PathGuard.next_available(dest_str)
            if dest_str != os.path.join(self.guard.archiveRoot, rel):
                self._logger.info("目标重名，改用：%s", dest_str)
            dest = Path(dest_str)
            dest.parent.mkdir(parents=True, exist_ok=True)

            # 原子写：临时文件（同目录）→ os.replace
            written = self._write_atomic(src, str(dest))
            if not written:
                result.errorCode = ERROR_CODES["E602"]
                return result
            self.createdPaths.append(str(dest))
            result.archivedPath = str(dest)

            # 写后哈希复核
            if not self.verify(result.sourceSha256, str(dest)):
                result.errorCode = ERROR_CODES["E603"]
                result.sha256 = HashUtil.try_file_sha256(dest)
                return result
            result.sha256 = HashUtil.file_sha256(dest)
            result.verified = True
            return result
        except OSError as exc:
            result.errorCode = ERROR_CODES["E601"]
            self._logger.warning("归档写入失败：%s", exc)
            return result
        except Exception as exc:  # noqa: BLE001 - 单文件异常隔离
            result.errorCode = ERROR_CODES["E602"]
            self._logger.exception("归档异常：%s", exc)
            return result

    def _write_atomic(self, src: str, dest: str) -> bool:
        """写临时文件再原子重命名；失败清理临时文件。"""
        dest_path = Path(dest)
        tmp_path = dest_path.with_name(f".{dest_path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with Path(src).open("rb") as source, tmp_path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            os.replace(tmp_path, dest_path)
            return True
        except OSError:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            return False

    @staticmethod
    def verify(src_hash: str, dest: str) -> bool:
        """写后 SHA-256 复核。"""
        if not src_hash:
            return False
        try:
            return HashUtil.file_sha256(dest) == src_hash
        except OSError:
            return False

    # ---------------------------------------------------------------- 回滚
    def rollback(self) -> list[str]:
        """回滚本批新建副本（仅删副本，原件永不删除）。

        Returns:
            已删除的副本路径列表。
        """
        removed: list[str] = []
        for path in list(self.createdPaths):
            try:
                p = Path(path)
                if p.exists():
                    p.unlink()
                    removed.append(path)
            except OSError as exc:
                self._logger.warning("回滚删除失败 %s：%s", path, exc)
        self.createdPaths = []
        if removed:
            self._logger.warning("已回滚 %d 个新建副本", len(removed))
        return removed


__all__ = ["Archiver", "ArchiveResult"]
