"""日志配置：文件 + 控制台双通道，异常堆栈落盘，日志带批次标记。"""
from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "invoice_organizer"
_CONFIGURED = False
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s %(message)s"


def configure_logging(data_dir: str | Path | None = None,
                      level: int = logging.INFO) -> logging.Logger:
    """配置并返回应用 logger（幂等）。

    Args:
        data_dir: 应用数据目录，日志文件写到其下 ``logs/invoice_organizer.log``。
        level: 日志级别。

    Returns:
        配置完成的 logger。
    """
    global _CONFIGURED
    logger = logging.getLogger(_LOGGER_NAME)
    if _CONFIGURED:
        return logger
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if data_dir is not None:
        log_dir = Path(data_dir) / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "invoice_organizer.log", encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            # 无写权限时仅保留控制台通道，不影响启动
            pass

    _CONFIGURED = True
    return logger


def get_logger(batch_id: str = "") -> logging.LoggerAdapter:
    """返回带批次标记的 logger adapter。

    Args:
        batch_id: 批次 ID，将作为 ``[batch=...]`` 前缀写入每条日志。
    """
    base = logging.getLogger(_LOGGER_NAME)
    prefix = f"[batch={batch_id}] " if batch_id else ""
    return logging.LoggerAdapter(base, {"batch": batch_id, "prefix": prefix})


__all__ = ["configure_logging", "get_logger"]
