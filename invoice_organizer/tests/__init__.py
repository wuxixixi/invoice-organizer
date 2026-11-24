"""测试包标识。

外部依赖（pdfplumber/pytesseract/openpyxl/pywebview/watchdog）均为可选，
核心用例不依赖 GUI 与各 OCR 引擎；OCR 相关降级路径由 monkeypatch/stub 覆盖。
"""
