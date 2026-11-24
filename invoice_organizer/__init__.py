"""invoice_organizer 包标识与全局常量。

本模块是跨文件共享知识（ARCHITECTURE 第 7 节）的唯一权威来源：
八字段键、置信状态、错误码、金额/日期口径常量均在此定义，其他模块必须引用，
不得就地硬编码，以保证全项目口径一致。

设计原则：所有解析结果均为“整理辅助建议”，最终判断权在人工。
"""
from __future__ import annotations

__version__ = "1.0.0"

# 表结构版本（配置 schemaVersion 当前值）
SCHEMA_VERSION: int = 1

# 八字段槽位键（顺序固定，唯一权威，见 ARCHITECTURE 7.2）
FIELD_KEYS: tuple[str, ...] = (
    "invoiceNo",
    "issueDate",
    "buyerName",
    "buyerTaxId",
    "sellerName",
    "sellerTaxId",
    "itemName",
    "amountCent",
)

# 模板/规则可用的安全元变量（见 ARCHITECTURE 7.2）
SAFE_TOKENS: tuple[str, ...] = ("batchId",)

# 白名单占位符 = 八字段 + 安全元变量
TEMPLATE_TOKENS: frozenset[str] = frozenset(FIELD_KEYS) | frozenset(SAFE_TOKENS)

# 置信状态字面值（见 ARCHITECTURE 7.2）
CONFIDENCE_HIGH: str = "high"
CONFIDENCE_LOW: str = "low"
CONFIDENCE_NEED_HUMAN: str = "need_human"

# 金额口径（见 ARCHITECTURE 7.5）
AMOUNT_SOURCE_EXCL_TAX: str = "totalExclTax"
AMOUNT_SOURCE_INCL_TAX: str = "totalInclTax"
AMOUNT_SOURCES: tuple[str, ...] = (AMOUNT_SOURCE_EXCL_TAX, AMOUNT_SOURCE_INCL_TAX)

# 未命中规则时的固定分类标签（见 ARCHITECTURE 7.5）
UNCATEGORIZED_LABEL: str = "未分类"

# 空值/看不清时前端展示的占位文本（见 ARCHITECTURE 7.2 / 7.5）
NEED_HUMAN_TEXT: str = "待人工确认"

# 统一社会信用代码合法字符集（见 ARCHITECTURE 7.3）
TAX_ID_CHARS: frozenset[str] = frozenset("0123456789ABCDEFGHJKLMNPQRTUWXY")
TAX_ID_LENGTH: int = 18

# 统一错误码（见 ARCHITECTURE 7.4）；code=0 表示成功（E000）
ERROR_CODES: dict[str, str] = {
    "E000": "成功",
    "E101": "写稳检测超时",
    "E102": "无匹配解析器 / 不支持类型",
    "E201": "同号同哈希重复",
    "E202": "同号异哈希冲突",
    "E203": "目标重名（自动追加序号或隔离）",
    "E301": "字段缺失 / 低置信",
    "E302": "日期无法归一",
    "E303": "金额解析失败",
    "E401": "OCR 超时（重试耗尽）",
    "E402": "无可用 OCR 引擎",
    "E501": "路径越界 / 软链接 / 非法字符",
    "E502": "输入与输出目录互相包含",
    "E601": "磁盘满 / 目录无权限",
    "E602": "原子写入失败（已回滚）",
    "E603": "写后哈希复核不一致",
    "E701": "配置校验失败 / schema 不兼容",
    "E702": "计划未确认禁止执行",
    "E801": "审计写入失败",
}

__all__ = [
    "__version__",
    "SCHEMA_VERSION",
    "FIELD_KEYS",
    "SAFE_TOKENS",
    "TEMPLATE_TOKENS",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_NEED_HUMAN",
    "AMOUNT_SOURCE_EXCL_TAX",
    "AMOUNT_SOURCE_INCL_TAX",
    "AMOUNT_SOURCES",
    "UNCATEGORIZED_LABEL",
    "NEED_HUMAN_TEXT",
    "TAX_ID_CHARS",
    "TAX_ID_LENGTH",
    "ERROR_CODES",
]
