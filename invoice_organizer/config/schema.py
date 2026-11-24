"""配置模型（dataclass）与校验。

字段命名严格对齐 ARCHITECTURE 3.1 类图与第 7 节共享知识。
``ProfileConfig.validate()`` 实现：枚举合法、目录不互相包含、模板占位符合法、
规则 priority 唯一、正则可编译。所有校验失败以字符串列表返回，供 bridge 映射 E701/E502。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any

from invoice_organizer import (
    AMOUNT_SOURCES,
    FIELD_KEYS,
    SAFE_TOKENS,
    SCHEMA_VERSION,
    TEMPLATE_TOKENS,
)

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


@dataclass
class WatchConfig:
    """输入目录监控配置。"""

    path: str = "data/inbox"
    recursive: bool = True
    settleSeconds: float = 1.0
    maxSettleWait: int = 60


@dataclass
class OutputConfig:
    """输出目录配置。"""

    archiveRoot: str = "data/archive"
    quarantine: str = "data/quarantine"


@dataclass
class MatchRule:
    """规则匹配条件：contains / regex / equals 三选一（可全部为空表示不匹配）。"""

    field: str = "itemName"
    contains: str = ""
    regex: str = ""
    equals: str = ""

    def matches(self, values: dict[str, str]) -> bool:
        """对给定字段值字典执行匹配，命中返回 True。

        Args:
            values: 字段名到字符串值的映射（来自 ``InvoiceRecord.to_row()``）。

        Returns:
            contains 子串命中、regex 搜索命中或 equals 全等命中任一即为 True。
        """
        raw: str = values.get(self.field, "") or ""
        if self.contains and self.contains in raw:
            return True
        if self.equals and raw == self.equals:
            return True
        if self.regex:
            try:
                if re.search(self.regex, raw):
                    return True
            except re.error:
                return False
        return False


@dataclass
class CategoryRule:
    """分类规则集合元素，按 priority 升序匹配。"""

    id: str = ""
    label: str = ""
    priority: int = 100
    match: MatchRule = field(default_factory=MatchRule)


@dataclass
class ExportConfig:
    """导出配置。"""

    template: str = "default"
    columns: list[str] = field(default_factory=lambda: list(FIELD_KEYS))
    encoding: str = "UTF-8-BOM"
    format: str = "xlsx"  # xlsx | csv


@dataclass
class ExecutionConfig:
    """执行闸门与 OCR 运行参数。"""

    previewRequired: bool = True
    preserveSource: bool = True
    dedupe: bool = True
    ocrRetry: int = 2
    ocrTimeoutSec: int = 20


@dataclass
class ProfileConfig:
    """一个命名档案的完整配置；每次保存 version 递增，历史不删。"""

    profileId: str = "default"
    version: int = 1
    schemaVersion: int = SCHEMA_VERSION
    watch: WatchConfig = field(default_factory=WatchConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    organizeBy: str = "invoiceNo"
    categories: list[CategoryRule] = field(default_factory=list)
    fields: list[str] = field(default_factory=lambda: list(FIELD_KEYS))
    amountSource: str = "totalExclTax"
    directoryTemplate: str = "{issueDate}/{sellerName}"
    filenameTemplate: str = "{issueDate}_{sellerName}_{amountCent}_{invoiceNo}"
    export: ExportConfig = field(default_factory=ExportConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    # ------------------------------------------------------------------ 校验
    def validate(self) -> list[str]:
        """执行全量配置校验。

        Returns:
            错误描述列表；空列表表示通过。目录互相包含使用消息前缀 ``E502:``，
            其余配置问题使用 ``E701:``，便于 bridge 直接映射错误码。
        """
        errors: list[str] = []

        if self.schemaVersion != SCHEMA_VERSION:
            errors.append(
                f"E701: schemaVersion={self.schemaVersion} 与当前 {SCHEMA_VERSION} 不兼容"
            )
        if not self.profileId or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", self.profileId):
            errors.append("E701: profileId 非法（限字母数字下划线连字符，1-64 位）")
        if self.version < 1:
            errors.append("E701: version 必须 >= 1")
        if self.amountSource not in AMOUNT_SOURCES:
            errors.append(f"E701: amountSource 非法：{self.amountSource}")
        if self.organizeBy not in FIELD_KEYS:
            errors.append(f"E701: organizeBy 非法：{self.organizeBy}")
        if not self.fields:
            errors.append("E701: fields 不得为空")
        unknown_fields = [f for f in self.fields if f not in FIELD_KEYS]
        if unknown_fields:
            errors.append(f"E701: fields 含非法字段：{unknown_fields}")

        if self.export.format not in ("xlsx", "csv"):
            errors.append(f"E701: export.format 非法：{self.export.format}")
        if self.export.encoding not in ("UTF-8-BOM", "UTF-8", "GBK"):
            errors.append(f"E701: export.encoding 非法：{self.export.encoding}")
        bad_columns = [c for c in self.export.columns if c not in TEMPLATE_TOKENS
                       and c not in ("categoryLabel", "archivedPath", "profileVersion",
                                     "fieldStatus", "sourcePath")]
        if bad_columns:
            errors.append(f"E701: export.columns 含非法列：{bad_columns}")

        if self.watch.settleSeconds < 0:
            errors.append("E701: settleSeconds 不得为负")
        if self.watch.maxSettleWait < 0:
            errors.append("E701: maxSettleWait 不得为负")
        if self.execution.ocrRetry < 0 or self.execution.ocrTimeoutSec < 1:
            errors.append("E701: ocrRetry/ocrTimeoutSec 取值非法")

        # 模板占位符合法性
        for name, tpl in (
            ("directoryTemplate", self.directoryTemplate),
            ("filenameTemplate", self.filenameTemplate),
        ):
            for token in _PLACEHOLDER_RE.findall(tpl or ""):
                if token not in TEMPLATE_TOKENS:
                    errors.append(f"E701: {name} 含非法占位符 {{{token}}}")

        # 规则 priority 唯一 + 正则可编译 + match.field 合法
        priorities: set[int] = set()
        rule_ids: set[str] = set()
        for idx, rule in enumerate(self.categories):
            if rule.priority in priorities:
                errors.append(f"E701: 规则 priority 重复：{rule.priority}")
            priorities.add(rule.priority)
            if not rule.id or rule.id in rule_ids:
                errors.append(f"E701: 第 {idx + 1} 条规则 id 为空或重复")
            rule_ids.add(rule.id)
            if rule.match.field not in TEMPLATE_TOKENS:
                errors.append(f"E701: 第 {idx + 1} 条规则 match.field 非法：{rule.match.field}")
            if rule.match.regex:
                try:
                    re.compile(rule.match.regex)
                except re.error as exc:
                    errors.append(f"E701: 第 {idx + 1} 条规则 regex 非法：{exc}")

        # 目录关系：watch.path 与 archiveRoot/quarantine 不得互相包含（含子目录）
        watch_p = self._safe_resolve(self.watch.path)
        for label, out_path in (
            ("archiveRoot", self.output.archiveRoot),
            ("quarantine", self.output.quarantine),
        ):
            out_p = self._safe_resolve(out_path)
            if self._contain_each_other(watch_p, out_p):
                errors.append(
                    f"E502: watch.path({self.watch.path}) 与 output.{label}({out_path}) 互相包含"
                )
        arch_p = self._safe_resolve(self.output.archiveRoot)
        quar_p = self._safe_resolve(self.output.quarantine)
        if self._contain_each_other(arch_p, quar_p):
            errors.append("E502: archiveRoot 与 quarantine 互相包含")

        return errors

    @staticmethod
    def _safe_resolve(raw: str) -> Path:
        """尽力解析为绝对路径；相对路径按当前工作目录解析。"""
        try:
            return Path(raw).expanduser().resolve(strict=False)
        except (OSError, ValueError):
            return Path(raw).absolute()

    @staticmethod
    def _contain_each_other(a: Path, b: Path) -> bool:
        """两路径互相包含（相等或一者为另一者祖先）返回 True。"""
        try:
            return a == b or a in b.parents or b in a.parents
        except (OSError, ValueError):
            return False

    # -------------------------------------------------------------- 序列化
    @classmethod
    def default(cls) -> "ProfileConfig":
        """返回一个已通过校验的默认配置（路径指向项目 data 目录）。"""
        return cls()

    def to_dict(self) -> dict[str, Any]:
        """转换为普通字典（键名与 JSON 一致）。"""
        return asdict(self)

    def to_json(self) -> str:
        """序列化为格式化 JSON 字符串（UTF-8，保留中文）。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileConfig":
        """从字典构造，缺失字段使用 dataclass 默认值，忽略未知键。"""
        data = dict(data or {})
        nested: dict[str, Any] = {}
        for key, nested_cls in (
            ("watch", WatchConfig),
            ("output", OutputConfig),
            ("export", ExportConfig),
            ("execution", ExecutionConfig),
        ):
            raw = data.get(key)
            nested[key] = nested_cls(**raw) if isinstance(raw, dict) else nested_cls()
        raw_categories = data.get("categories") or []
        categories: list[CategoryRule] = []
        for item in raw_categories:
            if not isinstance(item, dict):
                continue
            match_raw = item.get("match") or {}
            match = MatchRule(**match_raw) if isinstance(match_raw, dict) else MatchRule()
            categories.append(CategoryRule(
                id=str(item.get("id", "")),
                label=str(item.get("label", "")),
                priority=int(item.get("priority", 100)),
                match=match,
            ))
        kwargs: dict[str, Any] = {
            k: v for k, v in data.items()
            if k not in ("watch", "output", "export", "execution", "categories")
        }
        kwargs.update(nested)
        kwargs["categories"] = categories
        allowed = {f.name for f in dataclass_fields(cls)}
        return cls(**{k: v for k, v in kwargs.items() if k in allowed})

    @classmethod
    def from_json(cls, text: str) -> "ProfileConfig":
        """从 JSON 字符串构造 ProfileConfig。"""
        return cls.from_dict(json.loads(text))


__all__ = [
    "WatchConfig",
    "OutputConfig",
    "MatchRule",
    "CategoryRule",
    "ExportConfig",
    "ExecutionConfig",
    "ProfileConfig",
]
