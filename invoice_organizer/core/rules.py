"""RuleEngine：按 priority 升序匹配分类规则，命中首条即停。

未命中标签固定为“未分类”（ARCHITECTURE 7.5）。
"""
from __future__ import annotations

from typing import Sequence

from invoice_organizer import UNCATEGORIZED_LABEL
from invoice_organizer.config.schema import CategoryRule
from invoice_organizer.models.invoice import InvoiceRecord


class RuleEngine:
    """分类规则引擎。"""

    def __init__(self, rules: Sequence[CategoryRule] | None = None) -> None:
        """初始化。

        Args:
            rules: 规则列表；内部按 priority 升序排序。
        """
        self.rules: list[CategoryRule] = sorted(list(rules or []), key=lambda r: r.priority)

    def classify(self, invoice: InvoiceRecord) -> tuple[str, str | None]:
        """对发票分类。

        Args:
            invoice: 发票记录。

        Returns:
            (label, ruleId)；未命中返回 (UNCATEGORIZED_LABEL, None)。
        """
        values = invoice.to_row()
        for rule in self.rules:
            if rule.match.matches(values):
                return rule.label, rule.id
        return UNCATEGORIZED_LABEL, None


__all__ = ["RuleEngine"]
