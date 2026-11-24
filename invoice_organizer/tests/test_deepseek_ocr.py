"""DeepSeek-OCR 适配器测试：全程 mock HTTP，绝不访问内网。"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from invoice_organizer.parsers import deepseek_ocr
from invoice_organizer.parsers.base import RawTextField
from invoice_organizer.parsers.deepseek_ocr import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    OCR_MAX_TOKENS,
    OCR_PROMPT,
    DeepSeekOcrAdapter,
)
from invoice_organizer.utils import net as net_module


class _FakeResponse:
    """最小 HTTP 响应替身（支持 with 语句）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        """返回响应体字节。"""
        return self._raw

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _FakeOpener:
    """记录请求的 opener 替身。"""

    def __init__(self, payload: dict[str, Any] | None = None,
                 error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.requests: list[Any] = []
        self.timeouts: list[int] = []

    def open(self, request: Any, timeout: int | None = None) -> _FakeResponse:
        """记录并返回假响应或抛出异常。"""
        self.requests.append(request)
        self.timeouts.append(int(timeout or 0))
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.payload)


def _ocr_response(text: str) -> dict[str, Any]:
    """构造 OpenAI 兼容响应。"""
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def test_recognize_returns_raw_text_field(tmp_path: Path) -> None:
    """图片识别产出 label 空、text 为票面文字、bbox 全 0 的 RawTextField。"""
    image = tmp_path / "ticket.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    opener = _FakeOpener(_ocr_response("发票号码：24312000000012345678\n价税合计 ¥1308.63"))
    adapter = DeepSeekOcrAdapter(timeoutSec=17)

    with patch.object(net_module, "direct_opener", return_value=opener):
        fields = adapter.recognize(image)

    assert len(fields) == 1
    assert isinstance(fields[0], RawTextField)
    assert fields[0].label == ""
    assert fields[0].page == 1
    assert fields[0].bbox == (0.0, 0.0, 0.0, 0.0)
    assert "24312000000012345678" in fields[0].text

    request = opener.requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == DEFAULT_ENDPOINT
    assert payload["model"] == DEFAULT_MODEL
    assert payload["max_tokens"] == OCR_MAX_TOKENS
    assert "temperature" not in payload
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"] == OCR_PROMPT == "输出图片中的所有文字。"
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image.read_bytes()
    assert "Authorization" not in request.headers
    assert opener.timeouts[0] == 17


def test_html_table_output_is_cleaned_to_plain_text(tmp_path: Path) -> None:
    """模型返回的 HTML 表格（含无闭合 <th>）被清洗为纯文本，保留字段文字。"""
    image = tmp_path / "table.png"
    image.write_bytes(b"fake-png")
    html_content = (
        '<table><tr><th colspan="1" rowspan="2">发票号码：123</th>'
        '<td>2024-11-24</td></tr><tr><td>合计 100.00</td></tr></table>'
        '<table><th colspan="1" rowspan="2" valign="top"></th>'
        '<th colspan="1" rowspan="2" valign="top">24312000000012345678</th></table>'
    )
    opener = _FakeOpener(_ocr_response(html_content))

    with patch.object(net_module, "direct_opener", return_value=opener):
        fields = DeepSeekOcrAdapter().recognize(image)

    assert len(fields) == 1
    text = fields[0].text
    assert "<" not in text and ">" not in text
    assert "table" not in text and "th" not in text
    for expected in ("发票号码：123", "2024-11-24", "合计 100.00",
                     "24312000000012345678"):
        assert expected in text


def test_recognize_supports_pil_image(tmp_path: Path) -> None:
    """PIL.Image 入参也能编码为多模态消息（无 PIL 时跳过）。"""
    pytest.importorskip("PIL.Image")
    from PIL import Image  # type: ignore[import-not-found]

    image = Image.new("RGB", (2, 2), color="white")
    opener = _FakeOpener(_ocr_response("票面文字"))

    with patch.object(net_module, "direct_opener", return_value=opener):
        fields = DeepSeekOcrAdapter().recognize(image)

    assert len(fields) == 1 and fields[0].text == "票面文字"
    payload = json.loads(opener.requests[0].data.decode("utf-8"))
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_direct_opener_forces_no_proxy() -> None:
    """必须构造 ProxyHandler({})，确保请求直连不经安全网关/系统代理。"""
    with patch.object(net_module.urllib.request, "ProxyHandler") as handler_cls, \
         patch.object(net_module.urllib.request, "build_opener") as build_opener:
        net_module.direct_opener()

    handler_cls.assert_called_once_with({})
    build_opener.assert_called_once_with(handler_cls.return_value)


def test_recognize_degrades_on_exception(tmp_path: Path) -> None:
    """HTTP 失败时返回空列表，不抛异常。"""
    image = tmp_path / "bad.png"
    image.write_bytes(b"bad")
    opener = _FakeOpener(error=OSError("network down"))

    with patch.object(net_module, "direct_opener", return_value=opener):
        assert DeepSeekOcrAdapter().recognize(image) == []


def test_recognize_empty_content_returns_empty(tmp_path: Path) -> None:
    """模型返回空文本时降级为空列表。"""
    image = tmp_path / "empty.png"
    image.write_bytes(b"x")
    opener = _FakeOpener({"choices": [{"message": {"content": ""}}]})

    with patch.object(net_module, "direct_opener", return_value=opener):
        assert DeepSeekOcrAdapter().recognize(image) == []


def test_backend_defaults_do_not_require_key() -> None:
    """适配器默认模型为 default，且构造不依赖 Key。"""
    adapter = DeepSeekOcrAdapter()
    assert adapter.model == "default"
    assert adapter.apiKey == ""
    assert deepseek_ocr.DEFAULT_MODEL == "default"
    assert net_module.direct_opener is not None
