"""OfdXmlAdapter：用 zipfile + ElementTree 解析 OFD 包内 XML，提取文本与坐标。

OFD 为 zip 容器，包含 OFD.xml 及各页 Page*.xml；文本通常在
``<ofd:TextObject>`` 下的 ``<ofd:TextCode>`` 元素，坐标在 ``<ofd:Boundary>``。
复杂版式字段标 need_human，不臆造。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from invoice_organizer import ERROR_CODES
from invoice_organizer.parsers.base import (
    ParseContext,
    ParseResult,
    ParserAdapter,
    RawTextField,
)


def _local_name(tag: str) -> str:
    """去掉 XML 命名空间前缀。"""
    return tag.split("}")[-1].split(":")[-1]


class OfdXmlAdapter(ParserAdapter):
    """OFD 版式文件解析器。"""

    name = "ofd_xml"

    def can_handle(self, path: str, header: bytes = b"") -> bool:
        """按扩展名 .ofd 或 zip 魔数判定。"""
        suffix = Path(path).suffix.lower()
        if suffix == ".ofd":
            return True
        if not header.startswith(b"PK"):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
            return any("OFD.xml" in n for n in names)
        except (OSError, zipfile.BadZipFile):
            return False

    def parse(self, path: str, ctx: ParseContext) -> ParseResult:
        """解析 OFD 包内 XML 文本。"""
        result = ParseResult(
            sourceHash=ctx.fileHash, sourcePath=path, pageCount=0, adapterName=self.name
        )
        try:
            with zipfile.ZipFile(path) as zf:
                page_files = sorted(
                    n for n in zf.namelist()
                    if n.lower().endswith(".xml")
                    and ("page" in n.lower() or "content" in n.lower())
                )
                if not page_files:
                    result.warnings.append(
                        f"{ERROR_CODES['E102']}: OFD 包内未找到页面 XML"
                    )
                    return result
                result.pageCount = len(page_files)
                for page_no, name in enumerate(page_files, start=1):
                    try:
                        root = ET.fromstring(zf.read(name))
                    except ET.ParseError as exc:
                        result.warnings.append(f"OFD 页 {name} XML 解析失败：{exc}")
                        continue
                    for elem in root.iter():
                        if _local_name(elem.tag) not in ("TextCode", "Text"):
                            continue
                        text = (elem.text or "").strip()
                        if not text:
                            continue
                        bbox = self._find_bbox(elem)
                        result.fields.append(
                            RawTextField(label="", text=text, page=page_no, bbox=bbox)
                        )
        except Exception as exc:  # noqa: BLE001 - 单文件异常隔离
            result.warnings.append(f"OFD 解析失败：{type(exc).__name__}: {exc}")
        return result

    @staticmethod
    def _find_bbox(elem: ET.Element) -> tuple[float, float, float, float]:
        """向上查找 Boundary 元素的 X/Y/Width/Height 作为 bbox。"""
        node: ET.Element | None = elem
        for _ in range(5):
            if node is None:
                break
            boundary = next(
                (child for child in node.iter() if _local_name(child.tag) == "Boundary"),
                None,
            )
            if boundary is not None:
                values: dict[str, float] = {}
                for child in boundary.iter():
                    key = _local_name(child.tag)
                    if key in ("X", "Y", "Width", "Height") and child.text:
                        try:
                            values[key] = float(child.text)
                        except ValueError:
                            values[key] = 0.0
                x = values.get("X", 0.0)
                y = values.get("Y", 0.0)
                return (x, y, x + values.get("Width", 0.0), y + values.get("Height", 0.0))
            node = None  # ElementTree 无 parent 指针，仅查当前子树
        return (0.0, 0.0, 0.0, 0.0)


__all__ = ["OfdXmlAdapter"]
