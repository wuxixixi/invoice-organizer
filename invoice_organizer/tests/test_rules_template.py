"""规则引擎命中顺序 + 模板占位符/路径安全测试。"""
from __future__ import annotations

import pytest

from invoice_organizer import NEED_HUMAN_TEXT, UNCATEGORIZED_LABEL
from invoice_organizer.config.schema import CategoryRule, MatchRule, ProfileConfig
from invoice_organizer.core.rules import RuleEngine
from invoice_organizer.core.template import TemplateEngine
from invoice_organizer.models.invoice import FieldSlot, InvoiceRecord, SourceRef
from invoice_organizer.parsers.base import ParseResult, RawTextField
from invoice_organizer.utils.paths import PathGuard, PathViolation


def _invoice() -> InvoiceRecord:
    """构造一张用于测试的发票。"""
    inv = InvoiceRecord(id="I1", invoiceNo="12345678", issueDate="2024-11-24", amountCent=123456)
    inv.set_field("invoiceNo", FieldSlot("invoiceNo", "12345678"))
    inv.set_field("issueDate", FieldSlot("issueDate", "2024-11-24"))
    inv.set_field("sellerName", FieldSlot("sellerName", "某某科技_<>|公司"))
    inv.add_source(SourceRef(fileHash="a" * 64, filePath="src.pdf"))
    return inv


def test_rule_priority_first_hit() -> None:
    """priority 升序，命中首条即停。"""
    rules = [
        CategoryRule("r1", "差旅", 10, MatchRule("itemName", contains="交通")),
        CategoryRule("r2", "办公", 20, MatchRule("itemName", contains="交通")),
    ]
    inv = _invoice()
    inv.set_field("itemName", FieldSlot("itemName", "交通费"))
    label, rule_id = RuleEngine(rules).classify(inv)
    assert label == "差旅"
    assert rule_id == "r1"


def test_rule_regex_and_equals() -> None:
    """regex / equals 匹配。"""
    inv = _invoice()
    inv.set_field("itemName", FieldSlot("itemName", "ABC123"))
    rules = [CategoryRule("r", "正则类", 1, MatchRule("itemName", regex=r"\d{3}"))]
    assert RuleEngine(rules).classify(inv) == ("正则类", "r")
    rules = [CategoryRule("e", "等值类", 1, MatchRule("itemName", equals="ABC123"))]
    assert RuleEngine(rules).classify(inv) == ("等值类", "e")


def test_rule_uncategorized() -> None:
    """未命中归入“未分类”。"""
    assert RuleEngine([]).classify(_invoice()) == (UNCATEGORIZED_LABEL, None)


def test_template_whitelist_and_sanitize() -> None:
    """白名单占位符替换，非法占位符忽略，非法字符清洗。"""
    cfg = ProfileConfig.default()
    cfg.directoryTemplate = "{issueDate}/{sellerName}"
    cfg.filenameTemplate = "{issueDate}_{sellerName}_{amountCent}_{invoiceNo}"
    engine = TemplateEngine(cfg)
    inv = _invoice()
    name = engine.render_name(inv, "B20241124-084100-3F2A")
    assert "2024-11-24" in name
    assert "1234.56" in name
    assert "<" not in name and "|" not in name
    assert name.endswith(".pdf")
    engine._replace("{evil}{issueDate}", inv, "B1")
    assert any("evil" in w for w in engine.warnings)


def test_template_empty_value_fallback() -> None:
    """空值回退“待人工确认”。"""
    engine = TemplateEngine(ProfileConfig.default())
    result = engine._replace("{invoiceNo}_{sellerName}", InvoiceRecord(), "B1")
    assert NEED_HUMAN_TEXT in result


def test_template_path_guard_reject() -> None:
    """模板渲染结果交 PathGuard 二次校验，越界被拒。"""
    guard = PathGuard("/tmp/archive_root")
    with pytest.raises(PathViolation):
        guard.resolve_inside("/tmp/archive_root", "/etc/passwd")
    with pytest.raises(PathViolation):
        guard.safe_join("/tmp/archive_root", "../escape")


def test_config_validation_directory_overlap() -> None:
    """输入与输出目录互相包含 → E502。"""
    cfg = ProfileConfig.default()
    cfg.watch.path = "/data/inbox"
    cfg.output.archiveRoot = "/data/inbox/archive"
    errors = cfg.validate()
    assert any(e.startswith("E502") for e in errors)
