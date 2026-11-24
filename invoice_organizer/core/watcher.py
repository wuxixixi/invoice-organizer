"""FileWatcher + StableFile：目录监控、写稳检测、轮询兜底、入队回调。

写稳检测（ARCHITECTURE 1.4 C1 / 3.4）：
- 两轮采样 size + mtime_ns 一致；
- 独占方式重试打开文件（Windows 用 CREATE_NO_WINDOW + 独占共享模式，POSIX 用 flock）探测无写句柄；
- 写稳前不入队；超过 maxSettleWait → E101 记待处理。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Callable

from invoice_organizer import ERROR_CODES
from invoice_organizer.config.schema import WatchConfig
from invoice_organizer.utils.hashutil import HashUtil
from invoice_organizer.utils.logging import get_logger

# 可处理的文件扩展名（写稳扫描过滤）
_WATCH_SUFFIXES = {".pdf", ".ofd", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


@dataclass
class StableFile:
    """写稳后的文件快照。"""

    path: str
    sha256: str
    size: int
    mtimeNs: int


class FileWatcher:
    """目录监控器：事件驱动 + 轮询兜底。"""

    def __init__(self, config: WatchConfig) -> None:
        """初始化。

        Args:
            config: 监控配置（路径/递归/写稳秒数/最大等待）。
        """
        self.config = config
        self.queue: Queue[StableFile] = Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback: Callable[[StableFile], None] | None = None
        self._pending: dict[str, float] = {}  # path -> 首次检测时间
        self._is_windows = sys.platform.startswith("win")
        self._logger = get_logger()

    # -------------------------------------------------------------- 生命周期
    def start(self, callback: Callable[[StableFile], None]) -> None:
        """启动监控后台线程。

        Args:
            callback: 写稳文件回调（接收 StableFile）。
        """
        self._callback = callback
        self._stop_event.clear()
        observer = self._try_start_watchdog()
        if observer is not None:
            self._logger.info("目录监控使用 watchdog 事件驱动")
            # watchdog 事件最终也由轮询器做写稳确认，双保险
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="watcher-poll")
        self._thread.start()

    def _try_start_watchdog(self):
        """尝试启动 watchdog（可选依赖），失败返回 None（走纯轮询兜底）。"""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            watcher_self = self

            class _Handler(FileSystemEventHandler):
                """把事件路径纳入 pending，由轮询器做写稳判定。"""

                def _mark(self, event) -> None:  # type: ignore[no-untyped-def]
                    if not event.is_directory:
                        path = str(getattr(event, "src_path", ""))
                        if Path(path).suffix.lower() in _WATCH_SUFFIXES:
                            watcher_self._pending.setdefault(path, time.monotonic())

                def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
                    self._mark(event)

                def on_modified(self, event) -> None:  # type: ignore[no-untyped-def]
                    self._mark(event)

                def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
                    dest = str(getattr(event, "dest_path", ""))
                    if Path(dest).suffix.lower() in _WATCH_SUFFIXES:
                        watcher_self._pending.setdefault(dest, time.monotonic())

            observer = Observer()
            observer.schedule(_Handler(), self.config.path, recursive=self.config.recursive)
            observer.start()
            self._observer = observer
            return observer
        except Exception as exc:  # noqa: BLE001 - 可选依赖/无 inotify 时优雅降级
            self._logger.info("watchdog 不可用，使用轮询兜底：%s", exc)
            self._observer = None
            return None

    def stop(self) -> None:
        """停止监控与 watchdog observer。"""
        self._stop_event.set()
        observer = getattr(self, "_observer", None)
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=3)
            except Exception:  # noqa: BLE001
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    # -------------------------------------------------------------- 轮询循环
    def _poll_loop(self) -> None:
        """轮询扫描目录，发现新文件并做写稳判定。"""
        settle = max(0.1, float(self.config.settleSeconds or 1.0))
        max_wait = max(settle, float(self.config.maxSettleWait or 60))
        while not self._stop_event.is_set():
            try:
                self._scan_once(settle, max_wait)
            except Exception as exc:  # noqa: BLE001 - 单轮异常不终止监控
                self._logger.warning("监控轮询异常：%s", exc)
            self._stop_event.wait(timeout=settle / 2)

    def _scan_once(self, settle: float, max_wait: float) -> None:
        """扫描一遍目录，把新文件纳入 pending 并检测写稳。"""
        root = Path(self.config.path)
        if not root.exists():
            return
        pattern = "**/*" if self.config.recursive else "*"
        for path in root.glob(pattern):
            if not path.is_file() or path.suffix.lower() not in _WATCH_SUFFIXES:
                continue
            key = str(path)
            if key in self._pending:
                continue
            self._pending[key] = time.monotonic()
        for key in list(self._pending.keys()):
            started = self._pending[key]
            # 写稳超时 → E101，移出 pending
            if time.monotonic() - started > max_wait:
                self._pending.pop(key, None)
                self._logger.warning("%s: 写稳超时 %s", ERROR_CODES["E101"], key)
                continue
            if self.is_settled(key):
                self._pending.pop(key, None)
                self._enqueue(key)

    def is_settled(self, path: str) -> bool:
        """判断文件是否写稳：两轮采样一致 + 无共享写句柄。"""
        if not self._sample_twice(path):
            return False
        if self._has_writer_handle(path):
            return False
        return True

    def _sample_twice(self, path: str) -> bool:
        """两轮采样 size + mtime_ns 一致（间隔 settleSeconds）。"""
        try:
            p = Path(path)
            s1 = p.stat()
        except OSError:
            return False
        settle = max(0.05, float(self.config.settleSeconds or 1.0) / 2)
        time.sleep(settle)
        try:
            s2 = p.stat()
        except OSError:
            return False
        return s1.st_size == s2.st_size and s1.st_mtime_ns == s2.st_mtime_ns

    def _has_writer_handle(self, path: str) -> bool:
        """探测是否存在其他进程的写句柄。"""
        if self._is_windows:
            return self._has_writer_windows(path)
        return self._has_writer_posix(path)

    @staticmethod
    def _has_writer_windows(path: str) -> bool:
        """Windows：以独占共享模式重试打开；被占用说明仍在写。"""
        try:
            import msvcrt  # type: ignore[import-not-found]

            flags = os.O_RDONLY
            if hasattr(os, "O_NOINHERIT"):
                flags |= os.O_NOINHERIT
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            fd = os.open(path, flags)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                return False
            finally:
                os.close(fd)
        except OSError:
            return True
        except Exception:  # noqa: BLE001 - 平台差异，保守认为无写句柄
            return False

    @staticmethod
    def _has_writer_posix(path: str) -> bool:
        """POSIX：尝试非阻塞独占 flock；失败说明被占用。"""
        try:
            import fcntl

            with open(path, "rb") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    return False
                except BlockingIOError:
                    return True
        except OSError:
            return False
        except ImportError:  # pragma: no cover
            return False

    def _enqueue(self, path: str) -> None:
        """计算哈希，生成 StableFile 入队并回调。"""
        try:
            stat = Path(path).stat()
            stable = StableFile(
                path=path, sha256=HashUtil.file_sha256(path),
                size=stat.st_size, mtimeNs=stat.st_mtime_ns,
            )
        except OSError as exc:
            self._logger.warning("无法计算写稳文件哈希：%s - %s", path, exc)
            return
        self.queue.put(stable)
        if self._callback:
            try:
                self._callback(stable)
            except Exception as exc:  # noqa: BLE001 - 单文件回调异常隔离
                self._logger.exception("处理写稳文件失败 %s：%s", path, exc)


__all__ = ["FileWatcher", "StableFile"]
