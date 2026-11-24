"""解析适配器抽象基类与数据结构。

解析器只产出结构化文本/坐标，不做业务判断（ARCHITECTURE 1.3）。
``parse`` 不得抛未捕获异常，失败返回空字段 + 告警。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class RawTextField:
    """原始文本字段：label（线索）、text、页码（从 1）、边界框。"""

    label: str = ""
    text: str = ""
    page: int = 1
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {"label": self.label, "text": self.text, "page": self.page,
                "bbox": list(self.bbox)}


@dataclass
class ParseResult:
    """解析结果：原始文本字段列表 + 来源元信息 + 告警。"""

    fields: list[RawTextField] = field(default_factory=list)
    sourceHash: str = ""
    sourcePath: str = ""
    pageCount: int = 1
    adapterName: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def all_text(self) -> str:
        """按页拼接全部文本（供字段提取用）。"""
        return "\n".join(f.text for f in self.fields if f.text)

    def is_empty(self) -> bool:
        """是否没有任何可用文本。"""
        return not any(f.text.strip() for f in self.fields)


@dataclass
class ParseContext:
    """解析上下文：文件哈希、路径、OCR 超时与重试。"""

    fileHash: str = ""
    filePath: str = ""
    timeoutSec: int = 20
    retry: int = 2


@runtime_checkable
class OcrBackend(Protocol):
    """OCR 后端协议（可替换：text-layer / tesseract / umi-ocr 占位）。"""

    name: str

    def recognize(self, image: Any) -> list[RawTextField]:
        """识别图像，返回原始文本字段列表。"""
        ...


class ParserAdapter(ABC):
    """解析器抽象基类。"""

    name: str = "base"

    @abstractmethod
    def can_handle(self, path: str, header: bytes = b"") -> bool:
        """判断是否能处理该文件（按扩展名 + 魔数）。"""
        raise NotImplementedError

    @abstractmethod
    def parse(self, path: str, ctx: ParseContext) -> ParseResult:
        """解析文件；失败返回带 warning 的空结果，不得抛未捕获异常。"""
        raise NotImplementedError

    @staticmethod
    def read_header(path: str, size: int = 512) -> bytes:
        """读取文件头用于魔数判定，失败返回空字节。"""
        try:
            with open(path, "rb") as handle:
                return handle.read(size)
        except OSError:
            return b""


__all__ = ["RawTextField", "ParseResult", "ParseContext", "OcrBackend", "ParserAdapter"]
