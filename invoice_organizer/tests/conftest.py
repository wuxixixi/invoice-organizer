"""pytest 公共 fixture：临时目录、内存数据库、样例 ParseResult。

外部解析库（pdfplumber / pytesseract / webview）均为可选依赖，
核心单测不依赖它们；相关 fixture 采用延迟导入，确保收集阶段不因缺库失败。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from invoice_organizer.config.schema import ProfileConfig
from invoice_organizer.storage.db import Database


@pytest.fixture
def tmp_watch(tmp_path: Path) -> Path:
    """临时输入（投放）目录。"""
    path = tmp_path / "watch"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_archive(tmp_path: Path) -> Path:
    """临时归档根目录。"""
    path = tmp_path / "archive"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def tmp_quarantine(tmp_path: Path) -> Path:
    """临时隔离目录。"""
    path = tmp_path / "quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def mem_db() -> Database:
    """内存 SQLite 数据库（已建表）。"""
    db = Database(":memory:")
    db.init_schema()
    return db


@pytest.fixture
def config(tmp_watch: Path, tmp_archive: Path, tmp_quarantine: Path) -> ProfileConfig:
    """基于临时目录的可用配置（validate() 应通过）。"""
    cfg = ProfileConfig.default()
    cfg.watch.path = str(tmp_watch)
    cfg.output.archiveRoot = str(tmp_archive)
    cfg.output.quarantine = str(tmp_quarantine)
    cfg.execution.previewRequired = True
    return cfg


@pytest.fixture
def golden_dir() -> Path:
    """黄金样本目录（不存在时返回空目录路径，测试据此跳过样本相关断言）。"""
    return Path(__file__).resolve().parent / "golden"


def load_golden(name: str) -> dict[str, Any]:
    """加载一份脱敏黄金样本 JSON（不存在时返回空字典）。"""
    path = Path(__file__).resolve().parent / "golden" / f"{name}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@pytest.fixture
def sample_parse_result():
    """字段齐全的样例 ParseResult（延迟导入，避免收集期依赖解析器模块）。"""
    from invoice_organizer.parsers.base import ParseResult, RawTextField

    fields = [
        RawTextField(label="发票号码", text="发票号码：24312000000012345678", page=1,
                     bbox=(10.0, 20.0, 200.0, 30.0)),
        RawTextField(label="开票日期", text="开票日期：2024年11月24日", page=1,
                     bbox=(10.0, 32.0, 200.0, 42.0)),
        RawTextField(label="购买方名称", text="名称：某某大学", page=1,
                     bbox=(10.0, 44.0, 200.0, 54.0)),
        RawTextField(label="购买方纳税人识别号",
                     text="纳税人识别号：123456789012345678", page=1,
                     bbox=(10.0, 56.0, 220.0, 66.0)),
        RawTextField(label="销售方名称", text="名称：某某科技有限公司", page=1,
                     bbox=(10.0, 68.0, 220.0, 78.0)),
        RawTextField(label="销售方纳税人识别号",
                     text="纳税人识别号：91310115MA1K3XXXXX", page=1,
                     bbox=(10.0, 80.0, 240.0, 90.0)),
        RawTextField(label="项目名称", text="*信息技术服务*技术服务费", page=1,
                     bbox=(10.0, 92.0, 240.0, 102.0)),
        RawTextField(label="合计", text="合计 ¥1,234.56", page=1,
                     bbox=(10.0, 104.0, 240.0, 114.0)),
        RawTextField(label="价税合计", text="价税合计（大写）壹仟叁佰圆整 ¥1308.63", page=1,
                     bbox=(10.0, 116.0, 260.0, 126.0)),
    ]
    return ParseResult(
        fields=fields, sourceHash="a" * 64, sourcePath="sample.pdf",
        pageCount=1, adapterName="test-fixture",
    )
