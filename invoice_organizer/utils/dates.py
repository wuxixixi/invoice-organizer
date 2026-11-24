"""日期工具：多格式归一为 YYYY-MM-DD。"""
from __future__ import annotations

import re
from datetime import date, datetime

_DATE_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, str, str]], ...] = (
    (re.compile(r"^(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})"), ("y", "m", "d")),
    (re.compile(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"), ("y", "m", "d")),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})"), ("y", "m", "d")),
)


class DateUtil:
    """日期归一工具（静态）。"""

    @staticmethod
    def normalize(text: str) -> str:
        """把常见票面日期归一为 ``YYYY-MM-DD``。

        支持 ``YYYY年MM月DD日`` / ``YYYY/MM/DD`` / ``YYYY-MM-DD`` / ``YYYYMMDD``
        以及中文全角数字。无法归一返回空字符串（调用方标 need_human）。

        Args:
            text: 原始日期文本，可含前后噪声。

        Returns:
            归一后的日期字符串或空字符串。
        """
        if not text:
            return ""
        s = str(text).translate({ord(ch): ord(ch) - 0xFEE0 for ch in "０１２３４５６７８９"})
        s = s.strip()
        # 提取第一个 4 位年份起始的片段
        match_head = re.search(r"\d{4}", s)
        if not match_head:
            return ""
        candidate = s[match_head.start():].strip()
        for pattern, _ in _DATE_PATTERNS:
            m = pattern.match(candidate)
            if not m:
                continue
            try:
                year, month, day = (int(g) for g in m.group(1, 2, 3))
                return date(year, month, day).isoformat()
            except ValueError:
                return ""
        return ""

    @staticmethod
    def is_valid(text: str) -> bool:
        """判断归一后是否为空。"""
        return bool(DateUtil.normalize(text))

    @staticmethod
    def now_iso() -> str:
        """返回当前 UTC ISO 时间戳。"""
        return datetime.utcnow().isoformat(timespec="seconds")


__all__ = ["DateUtil"]
