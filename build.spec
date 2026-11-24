# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件 exe 打包配置（Windows）。

在源码根目录执行：pyinstaller build.spec --noconfirm --clean
资源按源码相对路径收集到 _MEIPASS，运行时由 utils/paths.py::resource_path 定位。
"""
from pathlib import Path

root = Path(SPECPATH)
ui_dir = root / "invoice_organizer" / "ui"
config_dir = root / "invoice_organizer" / "config"

ui_data = [
    (str(path), "invoice_organizer/ui")
    for path in sorted(ui_dir.glob("*"))
    if path.suffix.lower() in (".html", ".js", ".css")
]
config_data = [
    (str(path), "invoice_organizer/config")
    for path in sorted(config_dir.glob("*.json"))
]

a = Analysis(
    [str(root / "invoice_organizer" / "__main__.py")],
    pathex=[str(root)],
    binaries=[],
    datas=ui_data + config_data,
    hiddenimports=["webview", "pdfplumber", "openpyxl"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="发票整理助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
