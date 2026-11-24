"""路径安全工具：清洗、真实路径解析、归档根越界校验、软链接拒绝、重名序号。

这是硬性安全约束（ARCHITECTURE 1.4 C6/C7、7.6）：
- ``safe_join`` 过滤 ``..``、绝对路径、Windows 保留字符与分隔符注入；
- ``resolve_inside`` 以 ``Path.resolve()`` 后必须落在根路径内，否则抛 ``PathViolation``(E501)；
- ``reject_symlink`` 拒绝符号链接；
- ``next_available`` 追加 ``_2``/``_3``，绝不覆盖。
"""
from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

from invoice_organizer import NEED_HUMAN_TEXT

# Windows 保留字符（文件名非法）：< > : " / \ | ? * 以及控制字符
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class PathViolation(ValueError):
    """路径越界/软链接/非法字符异常，对应错误码 E501。"""

    def __init__(self, message: str = "路径越界或非法") -> None:
        super().__init__(message)
        self.error_code = "E501"
        self.message = message


class PathGuard:
    """归档路径守卫。"""

    def __init__(self, archive_root: str | Path, quarantine: str | Path | None = None) -> None:
        """初始化守卫。

        Args:
            archive_root: 归档根目录；所有目标必须位于其内。
            quarantine: 隔离区目录（可选），用于超策略重名等。
        """
        self.archiveRoot = str(archive_root)
        self.quarantine = str(quarantine) if quarantine else str(Path(archive_root) / "quarantine")
        self._root_resolved = Path(self.archiveRoot).expanduser().resolve(strict=False)

    # ------------------------------------------------------------ 清洗
    @staticmethod
    def sanitize_segment(segment: str) -> str:
        """清洗单个路径段：非法字符→``_``，保留字符名加前缀，空值回退占位文本。

        Args:
            segment: 原始路径段（目录名或文件名）。

        Returns:
            安全的路径段。
        """
        raw = str(segment or "").strip()
        # 去掉首尾空白与结尾的点（Windows 不允许）
        cleaned = _INVALID_CHARS_RE.sub("_", raw).strip(" .")
        if not cleaned:
            cleaned = NEED_HUMAN_TEXT
        # Windows 保留设备名
        stem = cleaned.split(".")[0].upper()
        if stem in _RESERVED_NAMES:
            cleaned = "_" + cleaned
        # 长度截断（含扩展名场景下限 120）
        return cleaned[:120]

    @staticmethod
    def _split_rel(rel: str) -> list[str]:
        """把相对路径拆为干净的路径段；拒绝绝对路径与 ``..``。

        Raises:
            PathViolation: 绝对路径、`..`、盘符注入。
        """
        if not rel:
            return []
        text = str(rel).strip().replace("\\", "/")
        # 拒绝明显绝对路径 / 盘符 / UNC
        if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
            raise PathViolation(f"拒绝绝对路径：{rel}")
        parts: list[str] = []
        for part in text.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise PathViolation(f"拒绝上级目录 ..：{rel}")
            parts.append(PathGuard.sanitize_segment(part))
        return parts

    # ------------------------------------------------------------ 拼接
    def safe_join(self, root: str | Path, rel: str) -> str:
        """把相对路径安全地拼到根目录下（仅做清洗与拼接，不落盘、不建目录）。

        Args:
            root: 根路径。
            rel: 相对路径（可含多层子目录）。

        Returns:
            拼接后的绝对路径字符串（已 resolve，但未做越界复核）。

        Raises:
            PathViolation: 绝对路径或 ``..`` 注入。
        """
        root_path = Path(root).expanduser().resolve(strict=False)
        parts = self._split_rel(rel)
        target = root_path.joinpath(*parts) if parts else root_path
        return str(target.resolve(strict=False))

    # -------------------------------------------------------- 越界复核
    def resolve_inside(self, root: str | Path, p: str | Path) -> str:
        """复核路径``p``解析后是否真实落在``root``内，且无软链接。

        Args:
            root: 允许的 Root 目录。
            p: 待校验路径（绝对或相对）。

        Returns:
            解析后的绝对路径字符串。

        Raises:
            PathViolation: 越界、软链接或非法。
        """
        root_path = Path(root).expanduser().resolve(strict=False)
        candidate = Path(p).expanduser()
        candidate_abs = candidate if candidate.is_absolute() else (root_path / candidate)
        resolved = candidate_abs.resolve(strict=False)
        try:
            inside = resolved == root_path or root_path in resolved.parents
        except (OSError, ValueError):  # pragma: no cover - 跨平台边界
            inside = False
        if not inside:
            raise PathViolation(f"路径越出归档根目录：{p} -> {resolved}")
        if self.reject_symlink(candidate_abs, stop_at=root_path):
            raise PathViolation(f"检测到符号链接，拒绝写入：{p}")
        return str(resolved)

    @staticmethod
    def reject_symlink(p: str | Path, stop_at: str | Path | None = None) -> bool:
        """检测路径链上是否存在符号链接。

        Args:
            p: 待检测路径（绝对或相对均可）。
            stop_at: 向上检测的停止目录（含）；为 None 时检测到文件系统根。

        Returns:
            存在软链接返回 True，否则 False。
        """
        path = Path(p).expanduser()
        if not path.is_absolute():
            path = path.absolute()
        stop = Path(stop_at).expanduser().resolve(strict=False) if stop_at else None
        cursor: Path | None = path
        while cursor is not None:
            try:
                if cursor.is_symlink():
                    return True
            except (OSError, ValueError):  # pragma: no cover
                return True
            if stop is not None and cursor.resolve(strict=False) == stop:
                break
            parent = cursor.parent
            if parent == cursor:  # 到达文件系统根
                break
            cursor = parent
        return False

    # ------------------------------------------------------------ 重名
    @staticmethod
    def next_available(path: str | Path) -> str:
        """若目标已存在，追加 ``_2``/``_3`` 直到可用，绝不覆盖。

        目录与文件统一处理：路径以存在即视为占用。

        Args:
            path: 期望目标路径。

        Returns:
            可用路径字符串。
        """
        target = Path(path)
        if not target.exists():
            return str(target)
        stem = target.stem
        suffix = target.suffix
        parent = target.parent
        counter = 2
        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return str(candidate)
            counter += 1
            if counter > 9999:  # 极端情况防御
                return str(parent / f"{stem}_{counter}{suffix}")

    # -------------------------------------------------------- 便捷方法
    def target_for(self, rel: str) -> str:
        """生成并最终复核一个归档目标路径（组合 safe_join + resolve_inside）。"""
        joined = self.safe_join(self.archiveRoot, rel)
        return self.resolve_inside(self.archiveRoot, joined)

    @staticmethod
    def is_within_child(parent: str | Path, child: str | Path) -> bool:
        """判断 child 是否位于 parent 之内（含相等）。用于配置目录关系校验。"""
        p = Path(parent).expanduser().resolve(strict=False)
        c = Path(child).expanduser().resolve(strict=False)
        return c == p or p in c.parents


__all__ = ["PathGuard", "PathViolation"]
