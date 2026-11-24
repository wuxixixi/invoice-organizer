"""程序入口：``python -m invoice_organizer``。

- ``--diag``：仅运行核心自检（不依赖 GUI / 外部解析库），用于验证安装与版本。
- 默认：初始化配置、数据库与日志，启动 pywebview 桌面窗口。

pywebview 采用延迟导入，确保无 GUI 环境下 --diag 与核心逻辑可用。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


def _project_root() -> Path:
    """返回项目根目录（本文件的上级目录）。"""
    return Path(__file__).resolve().parent.parent


def run_diag() -> int:
    """运行核心自检：版本、常量、配置校验、哈希/日期/金额、建表。

    Returns:
        进程退出码：0 表示全部通过，1 表示存在失败项。
    """
    from invoice_organizer import (
        ERROR_CODES,
        FIELD_KEYS,
        SCHEMA_VERSION,
        __version__,
    )
    from invoice_organizer.config.loader import ProfileLoader
    from invoice_organizer.config.schema import ProfileConfig
    from invoice_organizer.utils.dates import DateUtil
    from invoice_organizer.utils.hashutil import HashUtil
    from invoice_organizer.utils.money import MoneyUtil
    from invoice_organizer.utils.paths import PathGuard
    from invoice_organizer.storage.db import Database

    failures: list[str] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            print(f"  [OK] {name}")
        except Exception as exc:  # noqa: BLE001 - 自检需捕获所有异常并汇报
            failures.append(name)
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")

    print(f"invoice_organizer v{__version__} 自检（schemaVersion={SCHEMA_VERSION}）")
    print(f"Python: {sys.version.split()[0]}")

    print("- 共享常量 -")
    check(
        "八字段键顺序",
        lambda: (_ for _ in ()).throw(AssertionError("字段键不符"))
        if len(FIELD_KEYS) != 8 or FIELD_KEYS[0] != "invoiceNo"
        else None,
    )
    check("错误码数量", lambda: (_ for _ in ()).throw(AssertionError(len(ERROR_CODES)))
          if len(ERROR_CODES) < 19 else None)

    print("- 配置 -")
    loader = ProfileLoader(_project_root() / "data")
    cfg = ProfileConfig.default()
    errors = cfg.validate()
    check("默认配置校验", lambda: (_ for _ in ()).throw(AssertionError("; ".join(errors)))
          if errors else None)
    check(
        "默认配置序列化往返",
        lambda: (_ for _ in ()).throw(AssertionError("roundtrip"))
        if ProfileConfig.from_json(cfg.to_json()).profileId != cfg.profileId
        else None,
    )
    check("配置加载器可用", lambda: loader.load("default"))

    print("- 工具 -")
    check("SHA-256 字节", lambda: (_ for _ in ()).throw(AssertionError("hash"))
          if HashUtil.bytes_sha256(b"abc") != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
          else None)
    check("日期归一", lambda: (_ for _ in ()).throw(AssertionError("date"))
          if DateUtil.normalize("2024年11月24日") != "2024-11-24" else None)
    check("元转分", lambda: (_ for _ in ()).throw(AssertionError("money"))
          if MoneyUtil.yuan_to_cent("1,234.56") != 123456 else None)
    check("路径守卫", lambda: PathGuard(str(_project_root() / "data" / "archive")))

    print("- 存储 -")
    check("SQLite 建表", lambda: Database(":memory:").init_schema())

    if failures:
        print(f"\n自检失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("\n自检全部通过。")
    return 0


def run_app(profile_id: str) -> int:
    """启动桌面窗口（pywebview 延迟导入，缺失时给出明确提示）。"""
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            "未安装 pywebview，无法启动桌面窗口。请先执行：pip install -r requirements.txt\n"
            f"详情：{exc}",
            file=sys.stderr,
        )
        return 2

    from invoice_organizer.config.loader import ProfileLoader
    from invoice_organizer.storage.db import Database
    from invoice_organizer.ui.app import AppWindow
    from invoice_organizer.ui.bridge import JsBridge
    from invoice_organizer.core.engine import OrganizeEngine

    root = _project_root()
    data_dir = root / "data"
    loader = ProfileLoader(data_dir)
    config = loader.load(profile_id)
    errors = config.validate()
    if errors:
        print("配置校验失败：" + "; ".join(errors), file=sys.stderr)
        return 3
    db = Database(data_dir / "invoice_organizer.db")
    db.init_schema()
    engine = OrganizeEngine(config=config, db=db, loader=loader, data_dir=data_dir)
    bridge = JsBridge(engine=engine)
    window = AppWindow(webview_module=webview, bridge=bridge, data_dir=data_dir)
    window.start()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行参数并分派到自检或应用启动。"""
    parser = argparse.ArgumentParser(prog="invoice_organizer", description="本地发票整理桌面工具")
    parser.add_argument("--diag", action="store_true", help="仅运行核心自检，不启动 GUI")
    parser.add_argument("--profile", default="default", help="要加载的配置档案 ID（默认 default）")
    args = parser.parse_args(argv)
    if args.diag:
        return run_diag()
    return run_app(args.profile)


if __name__ == "__main__":
    raise SystemExit(main())
