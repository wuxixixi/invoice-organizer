"""DeepSeek-OCR-2 适配器：内网直连、无需 Key、模型标识默认 ``default``。

接口为 OpenAI 兼容 chat/completions，图片以多模态 data URI 传入。
模型输出票面全部可见文字，包装为单个 ``RawTextField`` 供下游正则/大模型提取；
任何异常一律降级为空列表，绝不阻塞整理流程。
"""
from __future__ import annotations

import base64
import html
import io
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from invoice_organizer.parsers.base import RawTextField
from invoice_organizer.utils.logging import get_logger
from invoice_organizer.utils.net import post_json

DEFAULT_ENDPOINT: str = "http://172.19.1.2:30080/v1/chat/completions"
DEFAULT_MODEL: str = "default"

# deepseek-ocr-2 是专用 OCR 模型：复杂中文指令会使其输出空 content，
# 实测极简提示可稳定返回非空（但常带 <table>/<th> 布局标记，见 _clean_content）。
OCR_PROMPT: str = "输出图片中的所有文字。"
OCR_MAX_TOKENS: int = 2048

# 这些标签仅承载布局，清洗时以换行替代，保留其中的文本
_BLOCK_TAGS: frozenset[str] = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "br", "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
})


class _HtmlTextExtractor(HTMLParser):
    """提取 HTML 中的可见文字，布局标签转换为换行。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """布局开始标签追加换行。"""
        del attrs
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """布局结束标签追加换行；容忍 <th>content 无闭合的情况。"""
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        """收集标签之间的文本。"""
        self._parts.append(data)


_EXT_MAP: dict[str, str] = {
    "jpg": "jpeg", "jpeg": "jpeg", "png": "png", "bmp": "bmp",
    "tif": "tiff", "tiff": "tiff", "webp": "webp", "gif": "gif",
}


class DeepSeekOcrAdapter:
    """DeepSeek-OCR-2 图片识别后端（实现 ``OcrBackend`` 协议）。"""

    name = "deepseek_ocr"

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeoutSec: int = 60,
        apiKey: str = "",
    ) -> None:
        """初始化适配器。

        Args:
            endpoint: chat/completions 完整地址。
            model: 模型标识，服务端默认标识为 ``default``。
            timeoutSec: 请求超时秒数。
            apiKey: 保留参数；本服务无需 Key，**绝不发送 Authorization 头**。
        """
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.model = model or DEFAULT_MODEL
        self.timeoutSec = max(1, int(timeoutSec or 60))
        self.apiKey = ""
        del apiKey

    @staticmethod
    def _encode_image(image: Any) -> tuple[str, str]:
        """把路径 / PIL.Image / bytes 编码为 ``(扩展名, base64)``。"""
        raw: bytes = b""
        ext = "png"
        if isinstance(image, (str, Path)):
            path = Path(image)
            raw = path.read_bytes()
            ext = _EXT_MAP.get(path.suffix.lower().lstrip("."), "png")
        elif isinstance(image, (bytes, bytearray)):
            raw = bytes(image)
        else:
            pil_format = str(getattr(image, "format", "") or "PNG").upper()
            ext = _EXT_MAP.get(pil_format.lower(), "png")
            buffer = io.BytesIO()
            image.save(buffer, format=pil_format if pil_format in ("PNG", "JPEG", "BMP", "TIFF", "WEBP") else "PNG")
            raw = buffer.getvalue()
        if not raw:
            raise ValueError("图片内容为空")
        return ext, base64.b64encode(raw).decode("ascii")

    @staticmethod
    def _content_of(response: dict[str, Any]) -> str:
        """从 OpenAI 兼容响应中取 ``choices[0].message.content``。"""
        choices = response.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return ""
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            return ""
        content: Any = message.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            return "\n".join(parts).strip()
        return ""

    @staticmethod
    def _clean_content(content: str) -> str:
        """把模型输出的 HTML 表格布局清洗为干净纯文本。

        实测 deepseek-ocr-2 即使提示不要表格也会返回 ``<table><th ...>文本``，
        且存在 ``<th>`` 无闭合标签的情况；HTMLParser 能保留其文本并去掉标签。
        """
        if not content:
            return ""
        text = content.strip()
        if "<" in text and ">" in text:
            parser = _HtmlTextExtractor()
            try:
                parser.feed(text)
                parser.close()
                text = "".join(parser._parts)
            except Exception:  # noqa: BLE001 - 畸形 HTML 用正则兜底
                text = re.sub(r"<[^>]*>", " ", text)
        text = html.unescape(text).replace("\xa0", " ").replace("\u3000", " ")
        lines: list[str] = []
        for line in text.splitlines():
            cleaned = re.sub(r"[ \t]+", " ", line).strip()
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines).strip()

    def recognize(self, image: Any) -> list[RawTextField]:
        """识别图片，返回单条包含全部票面文字的字段；异常返回空列表。"""
        logger = get_logger()
        try:
            ext, encoded = self._encode_image(image)
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/{ext};base64,{encoded}"}},
                    ],
                }],
                "max_tokens": OCR_MAX_TOKENS,
            }
            # 不传 headers：本服务无需 Key；utils.net 内部强制 ProxyHandler({}) 直连。
            # 不传 temperature：实测 0 会触发长提示空输出；本极简提示无需温度参数。
            response = post_json(self.endpoint, payload, timeoutSec=self.timeoutSec)
            text = self._clean_content(self._content_of(response))
            if not text:
                return []
            return [RawTextField(label="", text=text, page=1,
                                 bbox=(0.0, 0.0, 0.0, 0.0))]
        except Exception as exc:  # noqa: BLE001 - 可选外部服务，失败必须降级
            logger.warning("DeepSeek OCR 识别失败，降级为空结果：%s: %s",
                           type(exc).__name__, exc)
            return []


__all__ = [
    "DeepSeekOcrAdapter", "DEFAULT_ENDPOINT", "DEFAULT_MODEL",
    "OCR_PROMPT", "OCR_MAX_TOKENS",
]
