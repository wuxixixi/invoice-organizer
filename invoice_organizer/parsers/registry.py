"""ParserRegistry：注册/发现适配器，按扩展名与魔数分发，未匹配返回 None。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from invoice_organizer import ERROR_CODES
from invoice_organizer.parsers.base import ParseContext, ParseResult, ParserAdapter
from invoice_organizer.parsers.image_ocr import ImageOcrAdapter
from invoice_organizer.parsers.ofd_xml import OfdXmlAdapter
from invoice_organizer.parsers.pdf_text import PdfTextAdapter


class ParserRegistry:
    """解析器注册表（可插拔，新增票种不改核心）。"""

    def __init__(self, adapters: Iterable[ParserAdapter] | None = None) -> None:
        """初始化注册表。

        Args:
            adapters: 适配器列表；None 时注册内置三适配器（PDF/OFD/图片）。
        """
        self.adapters: list[ParserAdapter] = []
        if adapters is None:
            self.register(PdfTextAdapter())
            self.register(OfdXmlAdapter())
            self.register(ImageOcrAdapter())
        else:
            for adapter in adapters:
                self.register(adapter)

    def register(self, adapter: ParserAdapter) -> None:
        """注册一个适配器。"""
        self.adapters.append(adapter)

    def pick(self, path: str) -> ParserAdapter | None:
        """按扩展名初选 + 内容魔数复核，返回第一个匹配的适配器。"""
        try:
            header = Path(path).read_bytes()[:512]
        except OSError:
            header = b""
        for adapter in self.adapters:
            try:
                if adapter.can_handle(path, header):
                    return adapter
            except Exception:  # noqa: BLE001 - 单个适配器探测异常不影响其他
                continue
        return None

    def parse(self, path: str, ctx: ParseContext) -> ParseResult:
        """选择适配器并解析；无匹配返回带 E102 告警的空结果。"""
        adapter = self.pick(path)
        if adapter is None:
            return ParseResult(
                sourceHash=ctx.fileHash, sourcePath=path, adapterName="none",
                warnings=[f"{ERROR_CODES['E102']}: 无匹配解析器，待人工确认"],
            )
        result = adapter.parse(path, ctx)
        result.sourceHash = ctx.fileHash or result.sourceHash
        result.sourcePath = path
        return result


__all__ = ["ParserRegistry"]
