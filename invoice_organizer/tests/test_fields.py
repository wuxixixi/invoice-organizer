"""八字段提取、置信标注、证据、日期/金额归一测试。"""
from __future__ import annotations

import pytest

from invoice_organizer import (
    AMOUNT_SOURCE_EXCL_TAX,
    AMOUNT_SOURCE_INCL_TAX,
    CONFIDENCE_HIGH,
    CONFIDENCE_NEED_HUMAN,
    FIELD_KEYS,
)
from invoice_organizer.core.fields import FieldExtractor
from invoice_organizer.models.invoice import Confidence
from invoice_organizer.parsers.base import ParseResult, RawTextField


def _sample_result(adapter: str = "pdf_text") -> ParseResult:
    """构造字段齐全的样例解析结果。"""
    return ParseResult(
        fields=[
            RawTextField("", "发票号码：24312000000012345678", 1, (1, 2, 3, 4)),
            RawTextField("", "开票日期：2024年11月24日", 1, (1, 2, 3, 4)),
            RawTextField("", "购买方 名称：某某大学", 1, (1, 2, 3, 4)),
            RawTextField("", "纳税人识别号：123456789012345678", 1, (1, 2, 3, 4)),
            RawTextField("", "销售方 名称：某某科技有限公司", 1, (1, 2, 3, 4)),
            RawTextField("", "纳税人识别号：91310115MA1K3XXXXX", 1, (1, 2, 3, 4)),
            RawTextField("", "*信息技术服务*技术服务费", 1, (1, 2, 3, 4)),
            RawTextField("", "合计 ¥1,234.56", 1, (1, 2, 3, 4)),
            RawTextField("", "价税合计（大写）壹仟叁佰圆整 ¥1308.63", 1, (1, 2, 3, 4)),
        ],
        sourceHash="b" * 64, sourcePath="sample.pdf", pageCount=1, adapterName=adapter,
    )


def test_extract_all_eight_fields(sample_parse_result: ParseResult) -> None:
    """八字段完整提取，键与顺序符合共享知识。"""
    inv = FieldExtractor().extract(sample_parse_result, AMOUNT_SOURCE_EXCL_TAX)
    assert tuple(s.key for s in inv.fields) == FIELD_KEYS
    assert inv.invoiceNo == "24312000000012345678"
    assert inv.issueDate == "2024-11-24"
    assert inv.get_field("buyerName").value == "某某大学"
    assert inv.get_field("sellerName").value == "某某科技有限公司"
    assert inv.get_field("itemName").value == "*信息技术服务*技术服务费"


def test_confidence_and_evidence(sample_parse_result: ParseResult) -> None:
    """文本层精确匹配为 high，且每字段带证据。"""
    inv = FieldExtractor().extract(sample_parse_result, AMOUNT_SOURCE_EXCL_TAX)
    assert inv.get_field("invoiceNo").confidence == Confidence.HIGH
    assert inv.get_field("invoiceNo").evidence.sourceFile == "sample.pdf"
    assert inv.get_field("invoiceNo").evidence.page == 1
    assert "发票号码" in inv.get_field("invoiceNo").evidence.rawText


def test_amount_source_switch(sample_parse_result: ParseResult) -> None:
    """默认不含税；显式配置价税合计取 130863 分。"""
    excl = FieldExtractor().extract(sample_parse_result, AMOUNT_SOURCE_EXCL_TAX)
    incl = FieldExtractor().extract(sample_parse_result, AMOUNT_SOURCE_INCL_TAX)
    assert excl.amountCent == 123456
    assert incl.amountCent == 130863
    assert excl.get_field("amountCent").confidence == Confidence.HIGH


def test_missing_fields_need_human() -> None:
    """缺失字段标 need_human，不臆造。"""
    result = ParseResult(fields=[RawTextField("", "无有效信息", 1, (0, 0, 0, 0))],
                         adapterName="pdf_text")
    inv = FieldExtractor().extract(result)
    for slot in inv.fields:
        assert slot.confidence == Confidence.NEED_HUMAN
        assert slot.value == ""
    assert inv.amountCent == 0


def test_tax_id_validation() -> None:
    """税号格式异常标 need_human。"""
    extractor = FieldExtractor()
    assert extractor._validate_tax_id("123456789012345678").value == CONFIDENCE_HIGH
    assert extractor._validate_tax_id("SHORT").value == CONFIDENCE_NEED_HUMAN
    assert extractor._validate_tax_id("I" * 18).value == CONFIDENCE_NEED_HUMAN  # I 不在字符集


def test_ocr_adapter_low_confidence() -> None:
    """OCR 来源字段置信降为 low。"""
    inv = FieldExtractor().extract(_sample_result(adapter="image_ocr:tesseract"))
    assert inv.get_field("sellerName").confidence == Confidence.LOW


def test_date_formats() -> None:
    """多种日期格式归一。"""
    from invoice_organizer.utils.dates import DateUtil

    assert DateUtil.normalize("2024/11/24") == "2024-11-24"
    assert DateUtil.normalize("20241124") == "2024-11-24"
    assert DateUtil.normalize("2024-11-24") == "2024-11-24"
    assert DateUtil.normalize("无效") == ""
