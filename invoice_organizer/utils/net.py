"""内网直连 HTTP 工具。

OCR 与大模型服务均部署在内网且**不得经过系统代理 / 安全网关**。
本模块是直连策略的唯一实现点：``ProxyHandler({})`` 显式禁用环境代理
（HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 等），避免敏感票据数据被转发。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any


def direct_opener() -> urllib.request.OpenerDirector:
    """构造不使用任何代理的 opener。

    Returns:
        仅含空 ``ProxyHandler`` 的 opener，保证请求直连目标地址。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post_json(
    endpoint: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeoutSec: int = 60,
    opener: urllib.request.OpenerDirector | None = None,
) -> dict[str, Any]:
    """POST JSON 并把响应解析为字典。

    Args:
        endpoint: 完整 HTTP(S) 地址。
        payload: 请求体（JSON 可序列化）。
        headers: 额外请求头（如 Authorization）。
        timeoutSec: 超时秒数。
        opener: 可注入的 opener，便于测试；None 时走 :func:`direct_opener`。

    Returns:
        响应 JSON 字典；响应体为空或非字典时返回空字典。

    Raises:
        OSError / urllib.error.URLError / json.JSONDecodeError: 由调用方捕获降级。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    all_headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    all_headers.update(headers or {})
    request = urllib.request.Request(endpoint, data=body, headers=all_headers, method="POST")
    target = opener if opener is not None else direct_opener()
    with target.open(request, timeout=max(1, int(timeoutSec))) as response:
        raw = response.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    parsed: Any = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["direct_opener", "post_json"]
