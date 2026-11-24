"""金额工具：元字符串→整数分、整数分→元展示、口径切换。

约定（ARCHITECTURE 7.3）：
- 内部一律整数分，禁止浮点存储；
- 展示 ``分/100`` 保留两位；
- 默认口径 totalExclTax（合计不含税），totalInclTax 才取价税合计；
- 支持千分位、全角数字/句号、「元」「¥」等噪声字符。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from invoice_organizer import AMOUNT_SOURCE_EXCL_TAX

# 仅对全角字符做转换：全角 U+FF01..U+FF5E 减 0xFEE0 得到对应半角；全角空格单独处理
_FULLWIDTH_MAP = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH_MAP[0x3000] = 0x20
_NOISE_RE = re.compile(r"[¥￥元,，\s]")


class MoneyUtil:
    """金额归一工具（静态）。"""

    @staticmethod
    def _normalize_text(text: str) -> str:
        """全角转半角并去除噪声字符。"""
        normalized = str(text).translate(_FULLWIDTH_MAP)
        return _NOISE_RE.sub("", normalized)

    @staticmethod
    def yuan_to_cent(text: str | int | float | Decimal,
                     source: str = AMOUNT_SOURCE_EXCL_TAX) -> int:
        """把元金额字符串转换为整数分。

        Args:
            text: 元金额，如 ``"1,234.56"`` / ``"￥1234.56元"`` / ``"１２３．４５"``。
            source: 金额口径（totalExclTax/totalInclTax），仅作语义标注，不影响换算数值；
                    保留该参数是为了与调用方（FieldExtractor）签名一致。

        Returns:
            整数分（四舍五入到分）；无法解析返回 ``0``。
        """
        del source  # 口径由 FieldExtractor 取值时决定，换算本身无关
        if isinstance(text, bool):
            return 0
        if isinstance(text, int):
            return int(text)  # 视为已转换的分
        if isinstance(text, Decimal):
            return int((text * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if isinstance(text, float):
            return int((Decimal(str(text)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        cleaned = MoneyUtil._normalize_text(str(text))
        if not cleaned:
            return 0
        try:
            value = Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return 0
        cents = (value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(cents)

    @staticmethod
    def cent_to_yuan(cent: int) -> str:
        """整数分转元展示字符串（保留两位，不用浮点避免精度问题）。"""
        cent = int(cent or 0)
        sign = "-" if cent < 0 else ""
        absolute = abs(cent)
        return f"{sign}{absolute // 100}.{absolute % 100:02d}"

    @staticmethod
    def pick_amount(excl_tax_cent: int, incl_tax_cent: int,
                    amount_source: str = AMOUNT_SOURCE_EXCL_TAX) -> int:
        """按配置口径在不含税/价税合计之间选择内部金额分。"""
        return incl_tax_cent if amount_source == "totalInclTax" else excl_tax_cent


__all__ = ["MoneyUtil"]
