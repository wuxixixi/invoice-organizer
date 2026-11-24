"""LlmClient：OpenAI 兼容 chat/completions 封装，用于票面八字段结构化。

强制内网直连（``ProxyHandler({})``），Key 由调用方运行时注入，绝不落盘；
任何网络/解析异常均返回空字典，保持「整理辅助建议」定位。
"""
from __future__ import annotations

import json
import re
import urllib.error
from typing import Any

from invoice_organizer import FIELD_KEYS
from invoice_organizer.utils.logging import get_logger
from invoice_organizer.utils.net import post_json

DEFAULT_ENDPOINT: str = "http://172.19.1.10:8080/api/v1/chat/completions"
DEFAULT_MODEL: str = "DeepSeek-V4-Flash-0731"

DEFAULT_PROMPT: str = (
    "你是发票字段整理助手。请从给定的发票文本中提取以下八个字段："
    "invoiceNo（发票号码）、issueDate（开票日期，YYYY-MM-DD）、"
    "buyerName（购买方名称）、buyerTaxId（购买方纳税人识别号）、"
    "sellerName（销售方名称）、sellerTaxId（销售方纳税人识别号）、"
    "itemName（项目名称）、amountCent（金额，元，如 1234.56）。"
    "只输出一个 JSON 对象，键为上述八个英文键名，值为字符串；"
    "旧版增值税票密码区叠印可能导致购买方税号乱码，无法确定时输出空字符串，不得猜测；"
    "不含税金额取「合计」行而非「价税合计」。"
    "无法确定的字段输出空字符串；不要输出 Markdown、注释或额外文字。"
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LlmClient:
    """票面文本结构化客户端。"""

    def __init__(
        self,
        endpoint: str,
        model: str,
        apiKey: str,
        timeoutSec: int = 60,
        prompt: str = "",
    ) -> None:
        """初始化。

        Args:
            endpoint: chat/completions 完整地址。
            model: 模型标识。
            apiKey: 运行时密钥（环境变量 / 本地密钥文件注入）。
            timeoutSec: 超时秒数。
            prompt: 系统提示词；为空时使用默认提示词。
        """
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.model = model or DEFAULT_MODEL
        self.apiKey = str(apiKey or "")
        self.timeoutSec = max(1, int(timeoutSec or 60))
        self.prompt = prompt or DEFAULT_PROMPT
        self._logger = get_logger()

    @staticmethod
    def _content_of(response: dict[str, Any]) -> str:
        """取 ``choices[0].message.content``（兼容字符串与分段列表）。"""
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
    def _parse_json(content: str) -> dict[str, Any]:
        """从模型输出中提取 JSON 对象；失败返回空字典。"""
        if not content:
            return {}
        candidate = _FENCE_RE.sub("", content.strip()).strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed: Any = json.loads(candidate[start:end + 1])
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict) and isinstance(parsed.get("fields"), dict):
            parsed = parsed["fields"]
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _to_fields(parsed: dict[str, Any]) -> dict[str, str]:
        """映射为八字段字符串字典；过滤未知键与空值。"""
        result: dict[str, str] = {}
        for key in FIELD_KEYS:
            value: Any = parsed.get(key, "")
            if key == "amountCent" and not value:
                value = parsed.get("amount", "")
            result[key] = "" if value is None else str(value).strip()
        return result if any(result.values()) else {}

    def structure_invoice(self, text: str) -> dict[str, str]:
        """把票面文本结构化成八字段字典；失败返回空字典。

        Args:
            text: 票面全文（来自 ParseResult.all_text）。

        Returns:
            八字段到字符串的映射；无可用结果时为空字典。
        """
        if not text or not text.strip():
            return {}
        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": text},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers: dict[str, str] = {}
        if self.apiKey:
            headers["Authorization"] = f"Bearer {self.apiKey}"
        try:
            try:
                response = post_json(
                    self.endpoint, payload, headers=headers, timeoutSec=self.timeoutSec
                )
            except urllib.error.HTTPError as exc:
                # 部分兼容服务不支持 response_format；去掉后重试一次。
                if exc.code not in (400, 404, 422):
                    raise
                payload.pop("response_format", None)
                response = post_json(
                    self.endpoint, payload, headers=headers, timeoutSec=self.timeoutSec
                )
            return self._to_fields(self._parse_json(self._content_of(response)))
        except Exception as exc:  # noqa: BLE001 - 外部服务失败不得影响整理流程
            self._logger.warning("大模型结构化失败，保持原提取结果：%s: %s",
                                 type(exc).__name__, exc)
            return {}


__all__ = ["LlmClient", "DEFAULT_ENDPOINT", "DEFAULT_MODEL", "DEFAULT_PROMPT"]
