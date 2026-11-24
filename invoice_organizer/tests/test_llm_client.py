"""大模型结构化与密钥安全测试：HTTP 全 mock，仅使用假 key。"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from invoice_organizer import FIELD_KEYS
from invoice_organizer.config.loader import ProfileLoader
from invoice_organizer.config.schema import LlmConfig, OcrConfig, ProfileConfig
from invoice_organizer.core.fields import FieldExtractor
from invoice_organizer.core.llm_client import LlmClient
from invoice_organizer.models.invoice import Confidence
from invoice_organizer.parsers.base import ParseResult, RawTextField
from invoice_organizer.utils import net as net_module


class _FakeResponse:
    """最小 HTTP 响应替身。"""

    def __init__(self, payload: dict[str, Any] | str) -> None:
        self._raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def read(self) -> bytes:
        """返回 UTF-8 响应体。"""
        return self._raw.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _FakeOpener:
    """记录请求头的 opener 替身。"""

    def __init__(self, payload: dict[str, Any] | str) -> None:
        self.payload = payload
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: int | None = None) -> _FakeResponse:
        """记录请求并返回假响应。"""
        self.requests.append(request)
        return _FakeResponse(self.payload)


def _make_client() -> LlmClient:
    """构造使用假 key 的客户端（绝不发真实请求）。"""
    return LlmClient("http://llm.invalid/v1/chat/completions",
                     "test-model", "test-key", timeoutSec=9)


def _response(fields: dict[str, str]) -> dict[str, Any]:
    """构造 OpenAI 兼容响应。"""
    return {"choices": [{"message": {"role": "assistant",
                                     "content": json.dumps(fields, ensure_ascii=False)}}]}


def test_structure_invoice_parses_eight_fields() -> None:
    """解析 JSON content 为八字段字典，并携带 Bearer 假 key。"""
    fields = {key: "2024-11-24" for key in FIELD_KEYS}
    opener = _FakeOpener(_response(fields))

    with patch.object(net_module, "direct_opener", return_value=opener):
        result = _make_client().structure_invoice("发票号码：24312000000012345678")

    assert result == fields
    request = opener.requests[0]
    assert request.get_header("Authorization") == "Bearer test-key"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "test-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["content"] == "发票号码：24312000000012345678"


def test_structure_invoice_handles_fenced_and_alias_amount() -> None:
    """Markdown 代码块与 amount 别名也能解析。"""
    content = "```json\n" + json.dumps({
        "invoiceNo": "87654321", "amount": "1234.56",
    }, ensure_ascii=False) + "\n```"
    opener = _FakeOpener({"choices": [{"message": {"content": content}}]})

    with patch.object(net_module, "direct_opener", return_value=opener):
        result = _make_client().structure_invoice("票面文本")

    assert result["invoiceNo"] == "87654321"
    assert result["amountCent"] == "1234.56"
    assert all(key in result for key in FIELD_KEYS)


def test_structure_invoice_returns_empty_on_bad_json_and_exception() -> None:
    """非法 JSON / 网络异常均返回空字典，不崩溃。"""
    bad = _FakeOpener({"choices": [{"message": {"content": "not-json"}}]})
    with patch.object(net_module, "direct_opener", return_value=bad):
        assert _make_client().structure_invoice("文本") == {}

    failing = _FakeOpener("{}")
    failing.open = lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline"))  # type: ignore[assignment]
    with patch.object(net_module, "direct_opener", return_value=failing):
        assert _make_client().structure_invoice("文本") == {}


def test_default_config_has_empty_keys_and_valid_endpoints() -> None:
    """默认配置包含 ocr/llm 两节，apiKey 均为空，端点与超时合法。"""
    raw = json.loads((ProfileLoader.default_config().to_json()))
    assert raw["ocr"]["apiKey"] == "" and raw["llm"]["apiKey"] == ""
    assert raw["ocr"]["provider"] == "deepseek_ocr"
    assert raw["ocr"]["model"] == "default"
    assert raw["llm"]["model"] == "DeepSeek-V4-Flash-0731"
    assert OcrConfig().endpoint and LlmConfig().endpoint
    assert ProfileConfig.default().validate() == []

    bad = ProfileConfig.default()
    bad.ocr.endpoint = ""
    bad.llm.timeoutSec = 0
    errors = bad.validate()
    assert any("ocr.endpoint" in e for e in errors)
    assert any("llm.timeoutSec" in e for e in errors)


def test_runtime_key_never_enters_config_snapshot() -> None:
    """运行时注入的假 key 不得进入 to_dict / to_json / 批次快照。"""
    config = ProfileConfig.default()
    config.llm.apiKey = "test-key"
    config.ocr.apiKey = "unused"
    assert config.to_dict()["llm"]["apiKey"] == ""
    assert config.to_dict()["ocr"]["apiKey"] == ""
    assert "test-key" not in config.to_json()
    assert "test-key" not in ProfileLoader.snapshot(config)


def test_engine_injects_key_from_env_or_local_file(tmp_path: Any,
                                                   monkeypatch: Any) -> None:
    """引擎从本地密钥文件/环境变量注入假 key，且不写入配置快照。"""
    from invoice_organizer.core.engine import OrganizeEngine
    from invoice_organizer.storage.db import Database

    (tmp_path / "secrets.local.json").write_text(
        json.dumps({"llm": {"apiKey": "test-key"}}), encoding="utf-8"
    )
    monkeypatch.delenv("INVOICE_LLM_KEY", raising=False)
    config = ProfileConfig.default()
    OrganizeEngine(config=config, db=Database(":memory:"), data_dir=tmp_path)
    assert config.llm.apiKey == "test-key"
    assert "test-key" not in config.to_json()

    monkeypatch.setenv("INVOICE_LLM_KEY", "env-test-key")
    second = ProfileConfig.default()
    OrganizeEngine(config=second, db=Database(":memory:"), data_dir=tmp_path)
    assert second.llm.apiKey == "env-test-key"
    assert "env-test-key" not in ProfileLoader.snapshot(second)


def test_field_extractor_llm_only_fills_missing_fields() -> None:
    """LLM 仅补全缺失字段、标 low + 证据 llm，不覆盖 high 字段。"""
    class _StubLlm:
        """固定返回结构化结果的桩。"""

        def structure_invoice(self, _text: str) -> dict[str, str]:
            return {
                "invoiceNo": "99999999", "issueDate": "2024-11-24",
                "buyerName": "大模型补全的公司", "buyerTaxId": "",
                "sellerName": "", "sellerTaxId": "", "itemName": "", "amountCent": "100.00",
            }

    result = ParseResult(
        fields=[
            RawTextField("", "发票号码：24312000000012345678", 1, (1, 2, 3, 4)),
            RawTextField("", "票面其他文字", 1, (1, 2, 3, 4)),
        ],
        sourcePath="scan.pdf", adapterName="pdf_text",
    )
    inv = FieldExtractor(config=ProfileConfig.default(), llm_client=_StubLlm()).extract(result)

    assert inv.invoiceNo == "24312000000012345678"  # 高置信不被覆盖
    assert inv.get_field("invoiceNo").confidence == Confidence.HIGH
    assert inv.invoiceNo == inv.get_field("invoiceNo").value
    assert inv.get_field("buyerName").value == "大模型补全的公司"
    assert inv.get_field("buyerName").confidence == Confidence.LOW
    assert inv.get_field("buyerName").evidence.sourceFile == "llm"
    assert inv.amountCent == 10000
    assert inv.get_field("amountCent").evidence.sourceFile == "llm"
