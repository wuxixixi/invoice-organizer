"""FieldExtractor：八字段槽位提取、正则/关键词规则、校验、证据与置信标注。

设计要点（ARCHITECTURE 1.4 C4 / 3.2 / 7.2/7.3）：
- 每个字段携带 value / evidence / confidence；
- 文本层精确 + 校验通过 → high；OCR 或模糊 → low；缺失/校验失败 → need_human；
- 低置信与待确认均进入待复核；
- 金额整数分，默认 totalExclTax；日期归一 YYYY-MM-DD；税号 18 位字符集校验；
- 多行项目合并，取第一有效项目名称并拼接其余。
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Sequence

from invoice_organizer import (
    AMOUNT_SOURCE_EXCL_TAX,
    AMOUNT_SOURCE_INCL_TAX,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_NEED_HUMAN,
    ERROR_CODES,
    FIELD_KEYS,
    TAX_ID_CHARS,
    TAX_ID_LENGTH,
)
from invoice_organizer.config.schema import ProfileConfig
from invoice_organizer.models.invoice import (
    Confidence,
    Evidence,
    FieldSlot,
    InvoiceRecord,
    ItemLine,
    SourceRef,
)
from invoice_organizer.parsers.base import ParseResult, RawTextField
from invoice_organizer.utils.dates import DateUtil
from invoice_organizer.utils.money import MoneyUtil

# 票号：8-20 位数字（电子票多为 20 位）
_INVOICE_NO_RE = re.compile(r"(?<![0-9])(\d{8,20})(?![0-9])")
_DATE_RE = re.compile(r"\d{4}\s*(?:年|[-/.])\s*\d{1,2}\s*(?:月|[-/.])\s*\d{1,2}\s*日?|\d{8}")
_MONEY_RE = re.compile(r"(?<![0-9])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?![0-9])")
_ITEM_RE = re.compile(r"\*[^*]{1,30}\*[^*]{1,60}")

_LABEL_HINTS: dict[str, tuple[str, ...]] = {
    "invoiceNo": ("发票号码", "发票号", "票号", "invoiceno", "no."),
    "issueDate": ("开票日期", "开票时间", "日期", "issuedate"),
    "buyerName": ("购买方名称", "购方名称", "购买方", "买方名称"),
    "buyerTaxId": ("购买方纳税人识别号", "购买方税号", "购方纳税人识别号"),
    "sellerName": ("销售方名称", "销方名称", "销售方", "卖方名称"),
    "sellerTaxId": ("销售方纳税人识别号", "销售方税号", "销方纳税人识别号"),
    "itemName": ("项目名称", "货物或应税劳务", "服务名称", "项目内容"),
}


class FieldExtractor:
    """从 ParseResult 提取八字段并标注证据与置信。"""

    def __init__(self, config: ProfileConfig | None = None,
                 llm_client: Any | None = None) -> None:
        """初始化。

        Args:
            config: 配置（用于 amountSource 与 llm.enabled）；None 时默认不含税。
            llm_client: 可选大模型结构化客户端；仅补全缺失/低置信字段。
        """
        self.config = config
        self.llm_client = llm_client

    def extract(self, result: ParseResult, amountSource: str = AMOUNT_SOURCE_EXCL_TAX) -> InvoiceRecord:
        """执行字段提取，返回 InvoiceRecord。

        Args:
            result: 解析器产出的结构化文本。
            amountSource: 金额口径（totalExclTax/totalInclTax）。
        """
        if self.config is not None and amountSource == AMOUNT_SOURCE_EXCL_TAX:
            amountSource = self.config.amountSource

        source_name = result.sourcePath or "unknown"
        fields: list[RawTextField] = [f for f in result.fields if f.text and f.text.strip()]
        is_ocr = "ocr" in (result.adapterName or "").lower()
        base_conf = Confidence.LOW if is_ocr else Confidence.HIGH
        all_text = "\n".join(f.text for f in fields)
        is_digital = "电子发票（普通发票）" in all_text
        is_legacy = "增值税" in all_text and not is_digital

        inv = InvoiceRecord(
            id="INV" + uuid.uuid4().hex[:16],
            invoiceNo="",
            issueDate="",
            amountCent=0,
            status="EXTRACTED",
        )

        # 来源引用
        if result.sourceHash:
            inv.add_source(SourceRef(
                fileHash=result.sourceHash, filePath=source_name, page=1,
                bbox=(0.0, 0.0, 0.0, 0.0),
            ))

        # 1) 发票号码
        inv.set_field("invoiceNo", self._extract_slot(
            fields, "invoiceNo", self._find_invoice_no, source_name, base_conf
        ))
        inv.invoiceNo = inv.get_field("invoiceNo").value

        # 2) 开票日期
        inv.set_field("issueDate", self._extract_slot(
            fields, "issueDate", self._find_date, source_name, base_conf
        ))
        inv.issueDate = inv.get_field("issueDate").value

        # 3-6) 购买方/销售方 名称与税号（按上下文线索 + 出现顺序区分买卖方）
        name_hits = self._find_party(fields, "Name")
        tax_hits = self._find_party(fields, "TaxId")
        if is_legacy:
            # 旧票税号与「购/销」角色字分属不同 token，按坐标重建归属
            geo = self._find_tax_ids_by_role_geometry(fields)
            buyer_tax_hits, seller_tax_hits = geo["buyer"], geo["seller"]
        else:
            buyer_tax_hits, seller_tax_hits = tax_hits[:1], tax_hits[1:2]
        for key, hits in (
            ("buyerName", name_hits[:1]),
            ("sellerName", name_hits[1:2]),
            ("buyerTaxId", buyer_tax_hits[:1]),
            ("sellerTaxId", seller_tax_hits[:1]),
        ):
            slot = self._slot_from_hits(key, hits, source_name, base_conf)
            inv.set_field(key, slot)

        # 买卖方税号相同必属错位：清空证据较弱的 buyer 槽，交 LLM/人工补全
        buyer_tax = inv.get_field("buyerTaxId")
        seller_tax = inv.get_field("sellerTaxId")
        if buyer_tax.value and seller_tax.value and buyer_tax.value == seller_tax.value:
            inv.set_field("buyerTaxId", FieldSlot(
                key="buyerTaxId", value="", confidence=Confidence.NEED_HUMAN,
                evidence=Evidence(sourceFile=source_name),
            ))

        # 7) 项目名称（多行合并）
        inv.set_field("itemName", self._extract_slot(
            fields, "itemName", self._find_item_name, source_name, base_conf
        ))

        # 8) 金额：默认合计不含税，可选价税合计
        amount_slot = self._extract_amount(fields, source_name, base_conf, amountSource)
        inv.set_field("amountCent", amount_slot)
        inv.amountCent = MoneyUtil.yuan_to_cent(amount_slot.value) if amount_slot.value else 0

        # 行项目合并
        inv.merge_items(self._find_items(fields))

        # 大模型结构化补全（仅缺失/低置信字段，永不覆盖 high 与已核验字段）
        self._augment_with_llm(inv, result, source_name)

        # 按权威八字段顺序排列槽位（买卖方名称/税号须落在固定位置）
        order = {key: idx for idx, key in enumerate(FIELD_KEYS)}
        inv.fields.sort(key=lambda slot: order.get(slot.key, len(FIELD_KEYS)))
        return inv

    # --------------------------------------------------------- 大模型补全
    @staticmethod
    def _normalize_llm_value(key: str, raw: str) -> str:
        """校验并归一大模型给出的字段值；不合法返回空字符串（不臆造）。"""
        value = str(raw or "").strip()
        if not value:
            return ""
        if key == "issueDate":
            return DateUtil.normalize(value)
        if key == "amountCent":
            cents = MoneyUtil.yuan_to_cent(value)
            return MoneyUtil.cent_to_yuan(cents) if cents > 0 else ""
        if key in ("buyerTaxId", "sellerTaxId"):
            cleaned = re.sub(r"\s", "", value).upper()
            return cleaned if FieldExtractor._is_valid_tax_id(cleaned) else ""
        if key == "invoiceNo":
            digits = re.sub(r"\D", "", value)
            return digits if 8 <= len(digits) <= 20 else ""
        return value

    def _augment_with_llm(self, inv: InvoiceRecord, result: ParseResult,
                          source_name: str) -> None:
        """用大模型结果补全缺失/低置信字段，证据标 ``llm``、置信 low。"""
        del source_name
        client = self.llm_client
        if client is None:
            return
        llm_config = getattr(self.config, "llm", None)
        if llm_config is not None and not getattr(llm_config, "enabled", True):
            return
        try:
            structured = client.structure_invoice(result.all_text)
        except Exception as exc:  # noqa: BLE001 - 补全失败必须保留原结果
            from invoice_organizer.utils.logging import get_logger
            get_logger().warning("LLM 字段补全失败，保持原结果：%s: %s",
                                 type(exc).__name__, exc)
            return
        if not isinstance(structured, dict) or not structured:
            return
        for key in FIELD_KEYS:
            raw = structured.get(key, "")
            if not raw:
                continue
            existing = inv.get_field(key)
            if existing.verified or existing.confidence == Confidence.HIGH:
                continue
            value = self._normalize_llm_value(key, str(raw))
            if not value:
                continue
            inv.set_field(key, FieldSlot(
                key=key, value=value, confidence=Confidence.LOW,
                evidence=Evidence(sourceFile="llm", rawText=str(raw)),
            ))
        # 同步冗余字段（与槽位保持一致，供去重/模板/导出使用）
        inv.invoiceNo = inv.get_field("invoiceNo").value
        inv.issueDate = inv.get_field("issueDate").value
        amount_slot = inv.get_field("amountCent")
        inv.amountCent = MoneyUtil.yuan_to_cent(amount_slot.value) if amount_slot.value else 0

    # ------------------------------------------------------- 通用槽位构造
    def _extract_slot(self, fields: Sequence[RawTextField], key: str, finder,
                      source_name: str, base_conf: Confidence) -> FieldSlot:
        """使用 finder 从字段中查找并构造 FieldSlot。"""
        hit = finder(fields)
        if hit is None:
            return FieldSlot(key=key, value="", confidence=Confidence.NEED_HUMAN,
                             evidence=Evidence(sourceFile=source_name))
        value, field, matched_text = hit
        confidence = self._confidence_for(key, value, base_conf)
        evidence = Evidence(
            sourceFile=source_name, page=field.page, bbox=field.bbox, rawText=matched_text
        )
        return FieldSlot(key=key, value=value, confidence=confidence, evidence=evidence)

    def _slot_from_hits(self, key: str, hits: list[tuple[str, RawTextField, str]],
                        source_name: str, base_conf: Confidence) -> FieldSlot:
        """从预查找的命中列表构造 FieldSlot（用于买卖方区分）。"""
        if not hits:
            return FieldSlot(key=key, value="", confidence=Confidence.NEED_HUMAN,
                             evidence=Evidence(sourceFile=source_name))
        value, field, matched_text = hits[0]
        confidence = self._confidence_for(key, value, base_conf)
        return FieldSlot(
            key=key, value=value, confidence=confidence,
            evidence=Evidence(sourceFile=source_name, page=field.page,
                              bbox=field.bbox, rawText=matched_text),
        )

    def _confidence_for(self, key: str, value: str, base_conf: Confidence) -> Confidence:
        """按字段校验结果决定最终置信状态。"""
        if not value:
            return Confidence.NEED_HUMAN
        if key in ("buyerTaxId", "sellerTaxId"):
            # 格式合法时沿用来源置信（文本层=high / OCR=low），非法才 need_human
            return base_conf if self._is_valid_tax_id(value) else Confidence.NEED_HUMAN
        if key == "issueDate":
            if not DateUtil.normalize(value):
                return Confidence.NEED_HUMAN
            try:
                year = int(str(value)[:4])
            except (ValueError, TypeError):
                return Confidence.NEED_HUMAN
            # 机器编号等 8 位数字会被当成 YYYYMMDD 归一，用年份范围拦截
            return base_conf if 1900 <= year <= 2100 else Confidence.NEED_HUMAN
        if key == "amountCent":
            return base_conf if MoneyUtil.yuan_to_cent(value) > 0 or value == "0.00" \
                else Confidence.NEED_HUMAN
        return base_conf

    @staticmethod
    def _is_valid_tax_id(value: str) -> bool:
        """统一社会信用代码格式校验（18 位且字符集合法）。

        仅判断“格式是否合法”，不决定置信状态；置信由 _confidence_for 结合来源综合判定。
        """
        cleaned = re.sub(r"\s", "", value).upper()
        if len(cleaned) != TAX_ID_LENGTH:
            return False
        return not any(ch not in TAX_ID_CHARS for ch in cleaned)

    def _validate_tax_id(self, value: str) -> Confidence:
        """税号格式校验原语：合法 HIGH，非法 NEED_HUMAN（保留供单独校验调用）。"""
        return Confidence.HIGH if self._is_valid_tax_id(value) else Confidence.NEED_HUMAN

    # ------------------------------------------------------------- finders
    @staticmethod
    def _priority_fields(fields: Sequence[RawTextField], hints: Sequence[str]
                         ) -> list[RawTextField]:
        """把含线索词的字段排在前面（保留原顺序作为次序依据）。"""
        prioritized: list[RawTextField] = []
        rest: list[RawTextField] = []
        for f in fields:
            lowered = f.text.lower()
            if any(h.lower() in lowered for h in hints):
                prioritized.append(f)
            else:
                rest.append(f)
        return prioritized + rest

    def _find_invoice_no(self, fields: Sequence[RawTextField]
                         ) -> tuple[str, RawTextField, str] | None:
        """查找发票号码：优先带标签行，其次纯长数字。"""
        for f in self._priority_fields(fields, _LABEL_HINTS["invoiceNo"]):
            lowered = f.text.lower()
            labeled = any(h.lower() in lowered for h in _LABEL_HINTS["invoiceNo"])
            for m in _INVOICE_NO_RE.finditer(f.text):
                candidate = m.group(1)
                if labeled or len(candidate) >= 8:
                    return candidate, f, f.text
        return None

    def _find_date(self, fields: Sequence[RawTextField]) -> tuple[str, RawTextField, str] | None:
        """查找开票日期并归一；跳过机器编号等非票面日期数字。"""
        for f in self._priority_fields(fields, _LABEL_HINTS["issueDate"]):
            if "机器编号" in f.text:
                continue
            for m in _DATE_RE.finditer(f.text):
                normalized = DateUtil.normalize(m.group(0))
                if normalized:
                    return normalized, f, f.text
        return None

    def _find_party(self, fields: Sequence[RawTextField], kind: str
                    ) -> list[tuple[str, RawTextField, str]]:
        """按上下文（购买方/销售方）与出现顺序提取买卖方名称或税号。"""
        results: list[tuple[str, RawTextField, str]] = []
        seen: set[str] = set()

        # 先处理带明确“购买方/销售方”上下文的字段
        for f in fields:
            lowered = f.text.lower()
            role = ""
            if any(k in lowered for k in ("购买方", "购方", "买方")):
                role = "buyer"
            elif any(k in lowered for k in ("销售方", "销方", "卖方")):
                role = "seller"
            if not role and "销" in lowered and "名称" in lowered:
                role = "seller"
            if not role and "购" in lowered and "名称" in lowered:
                role = "buyer"
            values = self._extract_party_values(f.text, kind)
            for value in values:
                if value in seen:
                    continue
                if role in ("buyer", "seller"):
                    results.append((value, f, f.text))
                    seen.add(value)

        # 再处理无上下文的“名称：/纳税人识别号：”行，按出现顺序补 buyer→seller
        for f in fields:
            lowered = f.text.lower()
            if any(k in lowered for k in ("购买方", "销售方", "购方", "销方")):
                continue
            for value in self._extract_party_values(f.text, kind):
                if value in seen:
                    continue
                results.append((value, f, f.text))
                seen.add(value)
        return results

    @staticmethod
    def _extract_party_values(text: str, kind: str) -> list[str]:
        """从一行文本中提取名称或税号值。"""
        values: list[str] = []
        if kind == "TaxId":
            # 18 位统一社会信用代码；加边界避免匹配到 20 位票号的子串
            for m in re.finditer(
                r"(?<![0-9A-Z])[0-9A-HJ-NP-RTUWXY]{18}(?![0-9A-Z])", text.upper()
            ):
                values.append(m.group(0))
            return values
        # 名称：匹配 “名称：xxx” / “名 称: xxx”
        for m in re.finditer(r"名\s*称\s*[:：]\s*(.+)", text):
            value = m.group(1).strip(" :：\t")
            value = re.split(r"\s{2,}|纳税人识别号|税号", value)[0].strip()
            if value and len(value) >= 2:
                values.append(value)
        return values

    @staticmethod
    def _find_tax_ids_by_role_geometry(fields: Sequence[RawTextField]
                                        ) -> dict[str, list[tuple[str, RawTextField, str]]]:
        """按「购/买/销/卖/售/方」角色字的几何距离归属税号（旧票专用）。

        旧票的角色字是独立 token，税号 token 自身不含上下文；以
        ``|Δtop|*3 + |Δx0|`` 取最近锚点，「方」再按 x0 就近归入购/销。
        """
        roles = ("购", "买", "销", "卖", "售", "方")
        anchors = [f for f in fields if f.text.strip() in roles]
        party = [f for f in anchors if f.text.strip() in ("购", "销")]
        result: dict[str, list[tuple[str, RawTextField, str]]] = {
            "buyer": [], "seller": [],
        }
        if not anchors:
            return result
        for f in fields:
            if f.text.strip() in roles:
                continue
            values = FieldExtractor._extract_party_values(f.text, "TaxId")
            if not values:
                continue
            x, y = f.bbox[0], f.bbox[1]
            anchor = min(anchors,
                         key=lambda a: abs(a.bbox[1] - y) * 3 + abs(a.bbox[0] - x))
            label = anchor.text.strip()
            if label in ("购", "买"):
                role = "buyer"
            elif label in ("销", "卖", "售"):
                role = "seller"
            else:
                base = min(party, key=lambda a: abs(a.bbox[0] - anchor.bbox[0])) \
                    if party else None
                role = "buyer" if base is not None and base.text.strip() == "购" else "seller"
            for value in values:
                result[role].append((value, f, f.text))
        return result

    def _find_item_name(self, fields: Sequence[RawTextField]) -> tuple[str, RawTextField, str] | None:
        """查找项目名称（含 `*类别*明细` 格式或多行标签）。"""
        for f in fields:
            m = _ITEM_RE.search(f.text)
            # 密码区叠印会产出 `*938*//-…` 伪项目名：必须含中文才算有效项目
            if m and re.search(r"[\u4e00-\u9fff]", m.group(0)):
                return m.group(0).strip(), f, f.text
        for f in self._priority_fields(fields, _LABEL_HINTS["itemName"]):
            m = re.search(r"(?:项目名称|货物或应税劳务[、,，]?服务名称|服务名称)\s*[:：]\s*(.+)", f.text)
            if m and m.group(1).strip():
                return m.group(1).strip(), f, f.text
        # 旧版票表头与项目名分行：取表头之后首个「≥2 中文且不含数字」的 token
        header = next((f for f in fields
                       if "货物或应税劳务" in f.text or "项目名称" in f.text), None)
        if header is not None:
            below = sorted((f for f in fields if f.bbox[1] > header.bbox[1] + 4),
                           key=lambda f: f.bbox[1])
            for f in below:
                text = f.text.strip()
                if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2 and not re.search(r"\d", text):
                    return text, f, f.text
        return None

    def _extract_amount(self, fields: Sequence[RawTextField], source_name: str,
                        base_conf: Confidence, amount_source: str) -> FieldSlot:
        """提取金额：默认取“合计”不含税，totalInclTax 取“价税合计”。"""
        # 旧版增值税票：「合/计」被拆词，小写金额是价税合计；改从行级「合计」行取值
        all_text = "\n".join(f.text for f in fields)
        if ("增值税" in all_text and "电子发票（普通发票）" not in all_text
                and amount_source != AMOUNT_SOURCE_INCL_TAX):
            legacy = self._find_legacy_excl_tax(fields)
            if legacy is not None:
                value, field = legacy
                cents = MoneyUtil.yuan_to_cent(value)
                confidence = self._confidence_for("amountCent", value, base_conf) if cents > 0 \
                    else Confidence.NEED_HUMAN
                return FieldSlot(
                    key="amountCent", value=value, confidence=confidence,
                    evidence=Evidence(sourceFile=source_name, page=field.page, bbox=field.bbox,
                                      rawText=f"合计不含税: {field.text}"),
                )
        incl_hit = self._find_amount_by_label(fields, ("价税合计",))
        excl_hit = self._find_amount_by_label(fields, ("合计", "金额合计", "小写"))

        if amount_source == AMOUNT_SOURCE_INCL_TAX:
            chosen = incl_hit or excl_hit
            key_note = "价税合计"
        else:
            # 避免把“价税合计”误当作普通“合计”
            chosen = excl_hit or incl_hit
            key_note = "合计不含税"
        if chosen is None:
            return FieldSlot(key="amountCent", value="", confidence=Confidence.NEED_HUMAN,
                             evidence=Evidence(sourceFile=source_name,
                                               rawText=f"未找到{key_note}金额"))
        value, field = chosen
        cents = MoneyUtil.yuan_to_cent(value)
        confidence = self._confidence_for("amountCent", value, base_conf) if cents > 0 \
            else Confidence.NEED_HUMAN
        return FieldSlot(
            key="amountCent", value=value, confidence=confidence,
            evidence=Evidence(sourceFile=source_name, page=field.page, bbox=field.bbox,
                              rawText=f"{key_note}: {field.text}"),
        )

    @staticmethod
    def _find_legacy_excl_tax(fields: Sequence[RawTextField]
                              ) -> tuple[str, RawTextField] | None:
        """旧版增值税票：行级「合计」行去空格后取不含税金额。"""
        for f in fields:
            if f.label != "line":
                continue
            m = re.search(r"合计¥?(\d+\.\d{2})", re.sub(r"\s+", "", f.text))
            if m:
                return m.group(1), f
        return None

    @staticmethod
    def _find_amount_by_label(fields: Sequence[RawTextField], labels: Sequence[str]
                              ) -> tuple[str, RawTextField] | None:
        """在含指定标签的字段中查找金额（取最后一个，通常合计在行尾）。"""
        for f in fields:
            lowered = f.text.lower()
            if not any(label in lowered for label in labels):
                continue
            if "价税合计" in lowered and "合计" in labels and len(labels) == 1:
                pass  # 明确找价税合计时正常
            elif "价税合计" in lowered and "价税合计" not in labels:
                continue  # 找普通合计时跳过价税合计行
            matches = list(_MONEY_RE.finditer(f.text))
            if not matches:
                continue
            last = matches[-1]
            amount = last.group(1).replace(",", "") + (
                "." + last.group(2) if last.group(2) else ""
            )
            return amount, f
        return None

    @staticmethod
    def _find_items(fields: Sequence[RawTextField]) -> list[ItemLine]:
        """合并多行项目为行项目列表。"""
        items: list[ItemLine] = []
        seen: set[str] = set()
        for f in fields:
            for m in _ITEM_RE.finditer(f.text):
                name = m.group(0).strip()
                if name in seen:
                    continue
                seen.add(name)
                items.append(ItemLine(name=name, spec="", quantity=1, amountCent=0))
        return items


__all__ = ["FieldExtractor"]
