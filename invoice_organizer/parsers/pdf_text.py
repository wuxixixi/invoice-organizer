"""PdfTextAdapter：pdfplumber 文本层直读，产出带坐标的原始字段。

pdfplumber 为可选依赖；缺失或解析失败时返回带告警的空结果（不崩溃）。
"""
from __future__ import annotations

from pathlib import Path

from invoice_organizer import ERROR_CODES
from invoice_organizer.parsers.base import (
    ParseContext,
    ParseResult,
    ParserAdapter,
    RawTextField,
)


class PdfTextAdapter(ParserAdapter):
    """PDF 文本层解析器。"""

    name = "pdf_text"

    def can_handle(self, path: str, header: bytes = b"") -> bool:
        """按扩展名 .pdf 或魔数 %PDF 判定。"""
        suffix = Path(path).suffix.lower()
        return suffix == ".pdf" or header.startswith(b"%PDF")

    def parse(self, path: str, ctx: ParseContext) -> ParseResult:
        """解析 PDF 文本层。"""
        result = ParseResult(
            sourceHash=ctx.fileHash, sourcePath=path, pageCount=0, adapterName=self.name
        )
        try:
            import pdfplumber  # type: ignore[import-not-found]
        except ImportError:
            result.warnings.append(f"{ERROR_CODES['E402']}: 未安装 pdfplumber，无法读取 PDF 文本层")
            return result
        try:
            with pdfplumber.open(path) as pdf:
                result.pageCount = len(pdf.pages)
                for page_no, page in enumerate(pdf.pages, start=1):
                    chars = page.chars or []
                    words = page.extract_words() or []
                    for word in words:
                        text = str(word.get("text", ""))
                        if not text:
                            continue
                        result.fields.append(RawTextField(
                            label="", text=text, page=page_no,
                            bbox=(float(word.get("x0", 0.0)), float(word.get("top", 0.0)),
                                  float(word.get("x1", 0.0)), float(word.get("bottom", 0.0))),
                        ))
                    if not words:
                        text = page.extract_text() or ""
                        if text:
                            result.fields.append(RawTextField(
                                label="", text=text, page=page_no,
                                bbox=(0.0, 0.0, float(getattr(page, "width", 0.0)),
                                      float(getattr(page, "height", 0.0))),
                            ))
                    if not chars and not words:
                        result.warnings.append(f"第 {page_no} 页无可提取文本层（可能为扫描件）")
        except Exception as exc:  # noqa: BLE001 - 单文件异常隔离
            result.warnings.append(f"PDF 解析失败：{type(exc).__name__}: {exc}")
        return result


__all__ = ["PdfTextAdapter"]
