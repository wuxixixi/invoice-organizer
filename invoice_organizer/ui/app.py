"""AppWindow：pywebview 窗口创建、加载本地 index.html、注入 bridge、生命周期。

pywebview 为可选依赖，采用构造注入（由 __main__ 延迟导入后传入），
保证核心模块与 pytest 在无 GUI 环境可正常运行。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from invoice_organizer.ui.bridge import JsBridge
from invoice_organizer.utils.logging import get_logger


class AppWindow:
    """桌面窗口封装。"""

    def __init__(self, webview_module: Any, bridge: JsBridge,
                 data_dir: str | Path | None = None,
                 title: str = "科研人员发票整理工具") -> None:
        """初始化。

        Args:
            webview_module: 已导入的 pywebview 模块（由入口延迟导入传入）。
            bridge: JsBridge 实例。
            data_dir: 数据目录（用于窗口状态持久化，可选）。
            title: 窗口标题。
        """
        self.webview = webview_module
        self.bridge = bridge
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.title = title
        self._window: Any = None
        self._logger = get_logger()

    def _inject_api(self) -> None:
        """把 bridge 公开方法注入为 ``window.pywebview.api.*``。"""
        if self._window is None:
            return
        self._window.expose(self.bridge.start_watching, "start_watching")
        self._window.expose(self.bridge.get_plan, "get_plan")
        self._window.expose(self.bridge.confirm_plan, "confirm_plan")
        self._window.expose(self.bridge.execute_batch, "execute_batch")
        self._window.expose(self.bridge.list_batches, "list_batches")
        self._window.expose(self.bridge.get_review, "get_review")
        self._window.expose(self.bridge.save_review, "save_review")
        self._window.expose(self.bridge.export, "export")
        self._window.expose(self.bridge.get_config, "get_config")
        self._window.expose(self.bridge.save_config, "save_config")
        self._window.expose(self.bridge.set_role, "set_role")

    def start(self) -> None:
        """创建并启动窗口（阻塞直到窗口关闭）。"""
        html_path = Path(__file__).with_name("index.html")
        self._window = self.webview.create_window(
            self.title, html_path.as_uri(),
            width=1180, height=780, min_size=(960, 640),
            resizable=True, debug=False,
        )
        self._inject_api()
        try:
            self._window.events.closed += self.on_closed
        except Exception:  # noqa: BLE001 - 不同版本事件接口差异
            pass
        self._logger.info("桌面窗口启动：%s", html_path)
        self.webview.start()

    def on_closed(self) -> None:
        """窗口关闭回调：停止监控。"""
        try:
            self.bridge.engine.watcher.stop()
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("关闭时停止监控失败：%s", exc)


__all__ = ["AppWindow"]
