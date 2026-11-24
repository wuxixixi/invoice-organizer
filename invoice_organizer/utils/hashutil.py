"""哈希工具：流式 SHA-256（大文件不整读）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


class HashUtil:
    """SHA-256 计算工具（静态）。"""

    @staticmethod
    def file_sha256(path: str | Path) -> str:
        """流式计算文件 SHA-256。

        Args:
            path: 文件路径。

        Returns:
            64 位小写十六进制摘要。

        Raises:
            FileNotFoundError: 文件不存在。
            OSError: 读取失败（如无权限）。
        """
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def bytes_sha256(data: bytes) -> str:
        """计算字节串 SHA-256。"""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def try_file_sha256(path: str | Path) -> str:
        """安全版：读取失败返回空字符串而非抛异常。"""
        try:
            return HashUtil.file_sha256(path)
        except (OSError, ValueError):
            return ""


__all__ = ["HashUtil"]
