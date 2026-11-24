"""图片 OCR 适配器与可替换后端。

- ``TextLayerOcrAdapter``：无 OCR 引擎时的降级后端，返回空字段（字段标 need_human）；
- ``TesseractOcrAdapter``：pytesseract 后端（需本机 tesseract 可执行文件）；
- ``UmiOcrAdapter``：本机 HTTP 占位适配器（离线环回，无需额外依赖）；
- ``ImageOcrAdapter``：受限子进程执行 OCR，超时 terminate，有限重试，绝不崩溃。
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Any

from invoice_organizer import ERROR_CODES
from invoice_organizer.parsers.base import (
    ParseContext,
    ParseResult,
    ParserAdapter,
    RawTextField,
)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"BM", b"II*\x00", b"MM\x00*")


# ------------------------------------------------------------------ 后端
class TextLayerOcrAdapter:
    """降级后端：不做 OCR，返回空字段（调用方标 need_human，E402）。"""

    name = "text_layer"

    def recognize(self, image: Any) -> list[RawTextField]:
        """返回空列表，表示无可用识别结果。"""
        return []


class TesseractOcrAdapter:
    """pytesseract 后端；未安装引擎时返回空字段并降级。"""

    name = "tesseract"

    def recognize(self, image: Any) -> list[RawTextField]:
        """用 tesseract 识别图像；失败返回空列表。"""
        try:
            import pytesseract  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            return []
        try:
            pil_image = image if isinstance(image, Image.Image) else Image.open(image)
            data = pytesseract.image_to_data(
                pil_image, lang="chi_sim+eng", output_type=pytesseract.Output.DICT
            )
            fields: list[RawTextField] = []
            texts = data.get("text", [])
            for idx, text in enumerate(texts):
                cleaned = str(text).strip()
                if not cleaned:
                    continue
                left = float(data.get("left", [0] * len(texts))[idx])
                top = float(data.get("top", [0] * len(texts))[idx])
                width = float(data.get("width", [0] * len(texts))[idx])
                height = float(data.get("height", [0] * len(texts))[idx])
                fields.append(RawTextField(
                    label="", text=cleaned, page=1,
                    bbox=(left, top, left + width, top + height),
                ))
            return fields
        except Exception:  # noqa: BLE001 - 无引擎/无语言包时优雅降级
            return []


class UmiOcrAdapter:
    """Umi-OCR 本机 HTTP 占位适配器（离线环回，接口预留）。"""

    name = "umi_ocr"

    def __init__(self, endpoint: str = "http://127.0.0.1:1224/api/ocr") -> None:
        """初始化。

        Args:
            endpoint: 本机 Umi-OCR HTTP 接口地址（默认环回端口）。
        """
        self.endpoint = endpoint

    def recognize(self, image: Any) -> list[RawTextField]:
        """调用本机接口识别；未启动或异常时返回空列表（不阻塞主程序）。"""
        try:
            import base64
            import json
            import urllib.request
            from pathlib import Path as _Path

            if isinstance(image, (str, _Path)):
                raw = _Path(image).read_bytes()
            else:
                raw = getattr(image, "tobytes", lambda: b"")()
            if not raw:
                return []
            payload = json.dumps({"image": base64.b64encode(raw).decode("ascii")}).encode("utf-8")
            req = urllib.request.Request(
                self.endpoint, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - 仅本机环回
                body = json.loads(resp.read().decode("utf-8"))
            fields: list[RawTextField] = []
            for item in (body.get("data") or {}).get("texts", []):
                box = item.get("box") or [0, 0, 0, 0]
                text = str(item.get("text", "")).strip()
                if text:
                    fields.append(RawTextField(
                        label="", text=text, page=1,
                        bbox=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                    ))
            return fields
        except Exception:  # noqa: BLE001 - 可选外部适配器，失败降级
            return []


# ------------------------------------------------------------ 子进程 worker
def _ocr_worker(path: str, backend_name: str, queue: Any) -> None:
    """子进程入口：执行 OCR 并把结果（字典列表）放入队列。"""
    try:
        if backend_name == "tesseract":
            backend: Any = TesseractOcrAdapter()
        else:
            backend = TextLayerOcrAdapter()
        fields = backend.recognize(path)
        queue.put([f.to_dict() for f in fields])
    except Exception as exc:  # noqa: BLE001 - 子进程异常不让主进程崩溃
        queue.put({"__error__": f"{type(exc).__name__}: {exc}"})


def _fields_from_dicts(items: list[dict[str, Any]]) -> list[RawTextField]:
    """把队列中的字典还原为 RawTextField。"""
    result: list[RawTextField] = []
    for item in items:
        if "__error__" in item:
            continue
        bbox = item.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        result.append(RawTextField(
            label=str(item.get("label", "")), text=str(item.get("text", "")),
            page=int(item.get("page", 1) or 1),
            bbox=tuple(float(v) for v in bbox),  # type: ignore[arg-type]
        ))
    return result


# ---------------------------------------------------------------- 适配器
class ImageOcrAdapter(ParserAdapter):
    """图片 OCR 适配器：受限子进程 + 超时终止 + 有限重试。"""

    def __init__(self, backend: Any | None = None) -> None:
        """初始化。

        Args:
            backend: OCR 后端；None 时按可用性自动选择 tesseract，否则降级 text-layer。
        """
        self.backend = backend if backend is not None else self._default_backend()

    @staticmethod
    def _default_backend() -> Any:
        """按可用性选择默认后端。"""
        try:
            import pytesseract  # noqa: F401
            return TesseractOcrAdapter()
        except ImportError:
            return TextLayerOcrAdapter()

    @property
    def name(self) -> str:
        """适配器名称（含后端名）。"""
        return f"image_ocr:{getattr(self.backend, 'name', 'unknown')}"

    def can_handle(self, path: str, header: bytes = b"") -> bool:
        """按扩展名或图片魔数判定。"""
        if Path(path).suffix.lower() in _IMAGE_SUFFIXES:
            return True
        return any(header.startswith(magic) for magic in _IMAGE_MAGIC)

    def parse(self, path: str, ctx: ParseContext) -> ParseResult:
        """解析图片；优先受限子进程，失败/超时则降级。"""
        result = ParseResult(
            sourceHash=ctx.fileHash, sourcePath=path, pageCount=1, adapterName=self.name
        )
        if isinstance(self.backend, TextLayerOcrAdapter):
            result.warnings.append(
                f"E402 {ERROR_CODES['E402']}: 无可用 OCR 引擎，字段降级为待人工确认"
            )
            return result
        attempts = max(1, int(ctx.retry or 1))
        for attempt in range(1, attempts + 1):
            fields, error = self.parse_in_subprocess(path, ctx)
            if error == "timeout":
                result.warnings.append(
                    f"E401 {ERROR_CODES['E401']}: OCR 第 {attempt}/{attempts} 次超时"
                )
                continue
            if error:
                result.warnings.append(f"OCR 失败：{error}")
                break
            result.fields = fields
            return result
        result.warnings.append(
            f"E402 {ERROR_CODES['E402']}: OCR 不可用，字段降级为待人工确认"
        )
        return result

    def parse_in_subprocess(self, path: str, ctx: ParseContext
                            ) -> tuple[list[RawTextField], str]:
        """在受限子进程中执行 OCR，超时 terminate。

        Returns:
            (字段列表, 错误信息)；错误信息为空表示成功，``"timeout"`` 表示超时。
        """
        ctx_queue: Any = multiprocessing.Queue()
        backend_name = getattr(self.backend, "name", "text_layer")
        process = multiprocessing.Process(
            target=_ocr_worker, args=(path, backend_name, ctx_queue), daemon=True
        )
        try:
            process.start()
            process.join(timeout=max(1, int(ctx.timeoutSec or 20)))
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
                if process.is_alive():  # pragma: no cover - 极端情况强杀
                    process.kill()
                    process.join(timeout=1)
                return [], "timeout"
            if not ctx_queue.empty():
                payload = ctx_queue.get(timeout=1)
                if isinstance(payload, dict) and "__error__" in payload:
                    return [], str(payload["__error__"])
                if isinstance(payload, list):
                    return _fields_from_dicts(payload), ""
            return [], "empty_result"
        except Exception as exc:  # noqa: BLE001 - 子进程异常隔离
            return [], f"{type(exc).__name__}: {exc}"
        finally:
            try:
                ctx_queue.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "ImageOcrAdapter",
    "TextLayerOcrAdapter",
    "TesseractOcrAdapter",
    "UmiOcrAdapter",
]
