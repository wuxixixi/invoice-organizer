"""TemplateEngine：白名单占位符纯字符串替换 + 非法字符清洗 + 空值回退。

安全约束（ARCHITECTURE 1.4 C6 / 7.5）：
- 仅八字段 + batchId 白名单；其他占位符忽略并告警；
- 禁 eval/脚本，纯字符串替换；
- 非法字符（<>:\"/\\|?* 控制字符）替换为下划线；
- 空值回退“待人工确认”；
- 渲染结果交由 PathGuard 二次校验（本类不落盘）。
"""
from __future__ import annotations

import re
from pathlib import Path

from invoice_organizer import NEED_HUMAN_TEXT, TEMPLATE_TOKENS
from invoice_organizer.config.schema import ProfileConfig
from invoice_organizer.models.invoice import InvoiceRecord
from invoice_organizer.utils.money import MoneyUtil

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)),
                   *(f"LPT{i}" for i in range(1, 10))}


class TemplateEngine:
    """目录名/文件名模板引擎。"""

    def __init__(self, config: ProfileConfig | None = None) -> None:
        """初始化。

        Args:
            config: 配置（提供模板字符串）；None 时默认按日期/销售方/金额/票号命名。
        """
        self.config = config
        self.warnings: list[str] = []

    def render_dir(self, invoice: InvoiceRecord, batchId: str) -> str:
        """渲染归档相对目录。"""
        template = self.config.directoryTemplate if self.config else "{issueDate}/{sellerName}"
        return self._replace(template, invoice, batchId)

    def render_name(self, invoice: InvoiceRecord, batchId: str) -> str:
        """渲染归档文件名（含扩展名，来自来源路径）。"""
        template = self.config.filenameTemplate if self.config \
            else "{issueDate}_{sellerName}_{amountCent}_{invoiceNo}"
        name = self._replace(template, invoice, batchId)
        suffix = self._source_suffix(invoice)
        if suffix and not name.lower().endswith(suffix.lower()):
            name = f"{name}{suffix}"
        return name

    def _source_suffix(self, invoice: InvoiceRecord) -> str:
        """从来源引用推断文件扩展名（保留原格式）。"""
        for source in invoice.sources:
            suffix = Path(source.filePath).suffix
            if suffix:
                return suffix
        return ".pdf"

    def _replace(self, template: str, invoice: InvoiceRecord, batchId: str) -> str:
        """执行白名单替换与清洗。"""
        self.warnings = []
        tokens = self._token_values(invoice, batchId)

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in TEMPLATE_TOKENS:
                self.warnings.append(f"忽略非法占位符 {{{key}}}")
                return ""
            value = tokens.get(key, "")
            return NEED_HUMAN_TEXT if value in ("", None) else str(value)

        rendered = _PLACEHOLDER_RE.sub(repl, template or "")
        parts = [p for p in rendered.replace("\\", "/").split("/") if p]
        safe_parts: list[str] = []
        for part in parts:
            cleaned = self._sanitize(part)
            safe_parts.append(cleaned if cleaned else NEED_HUMAN_TEXT)
        return "/".join(safe_parts)

    @staticmethod
    def _sanitize(text: str) -> str:
        """清洗单个路径段。"""
        cleaned = _INVALID_CHARS_RE.sub("_", str(text).strip()).strip(" .")
        cleaned = re.sub(r"\.{2,}", "_", cleaned)
        if not cleaned:
            return ""
        stem = cleaned.split(".")[0].upper()
        if stem in _RESERVED_NAMES:
            cleaned = "_" + cleaned
        return cleaned[:120]

    @staticmethod
    def _token_values(invoice: InvoiceRecord, batchId: str) -> dict[str, str]:
        """构造模板变量映射（金额以元展示）。"""
        values: dict[str, str] = {key: "" for key in TEMPLATE_TOKENS}
        for slot in invoice.fields:
            if slot.key in values:
                values[slot.key] = slot.value
        values["invoiceNo"] = invoice.invoiceNo or values.get("invoiceNo", "")
        values["issueDate"] = invoice.issueDate or values.get("issueDate", "")
        values["amountCent"] = MoneyUtil.cent_to_yuan(invoice.amountCent)
        values["batchId"] = batchId
        return values


__all__ = ["TemplateEngine"]
