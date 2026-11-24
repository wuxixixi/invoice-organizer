"""PdfTextAdapter：pdfplumber 文本层直读，产出带坐标的原始字段。

pdfplumber 为可选依赖；缺失或解析失败时返回带告警的空结果（不崩溃）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from invoice_organizer import ERROR_CODES
from invoice_organizer.parsers.base import (
    ParseContext,
    ParseResult,
    ParserAdapter,
    RawTextField,
)


def _group_lines(words: list[dict[str, Any]]
                 ) -> list[tuple[str, tuple[float, float, float, float]]]:
    """按 top 分桶（约 ±3px）、x0 排序、空格拼接，重建行级文本。

    旧版增值税票的词级 token 常把「开票日期:2017 / 年07 / 月02日」「合 / 计」拆散，
    行级文本用于还原完整标签与数值；以 ``label="line"`` 与词级字段区分。
    """
    buckets: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        buckets.setdefault(int(round(float(word.get("top", 0.0)) / 3.0)), []).append(word)
    lines: list[tuple[str, tuple[float, float, float, float]]] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda w: float(w.get("x0", 0.0)))
        text = " ".join(t for w in group if (t := str(w.get("text", "")).strip()))
        if not text:
            continue
        x0 = min(float(w.get("x0", 0.0)) for w in group)
        x1 = max(float(w.get("x1", x0)) for w in group)
        top = min(float(w.get("top", 0.0)) for w in group)
        bottom = max(float(w.get("bottom", top)) for w in group)
        lines.append((text, (x0, top, x1, bottom)))
    return lines


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
                    # 行级字段追加在词级字段之后：既有「首个命中」顺序不变
                    for line_text, line_bbox in _group_lines(words):
                        result.fields.append(RawTextField(
                            label="line", text=line_text, page=page_no, bbox=line_bbox,
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


__all__ = ["PdfTextAdapter", "_group_lines"]
