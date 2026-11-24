"""ExportEngine：Excel(openpyxl)/CSV(UTF-8-BOM) 台账、批次报告、待复核清单。

导出规格（ARCHITECTURE 5.5 / 7.5）：
- 列序可配置，默认八字段 + 分类标签 + 归档路径 + 批次 ID + 配置版本 + 字段状态；
- CSV 默认 UTF-8-BOM（Excel 中文不乱码）；金额以元展示（内部整数分）；
- openpyxl 缺失时 xlsx 自动降级为 CSV，不崩溃。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Sequence

from invoice_organizer import ERROR_CODES, FIELD_KEYS
from invoice_organizer.config.schema import ExportConfig
from invoice_organizer.models.batch import Batch, BatchReport
from invoice_organizer.models.invoice import InvoiceRecord
from invoice_organizer.utils.logging import get_logger

_DEFAULT_COLUMNS = list(FIELD_KEYS) + [
    "categoryLabel", "archivedPath", "batchId", "profileVersion", "fieldStatus"
]


class ExportEngine:
    """台账与报告导出引擎。"""

    def __init__(self, config: ExportConfig | None = None,
                 output_dir: str | Path | None = None) -> None:
        """初始化。

        Args:
            config: 导出配置；None 时用默认。
            output_dir: 导出目录；None 时使用当前目录下的 exports。
        """
        self.config = config or ExportConfig()
        self.output_dir = Path(output_dir) if output_dir else Path("exports")
        self._logger = get_logger()

    def _ensure_dir(self) -> Path:
        """确保导出目录存在。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir

    # -------------------------------------------------------------- 台账
    def export_ledger(self, invoices: Sequence[InvoiceRecord], batch: Batch | None = None,
                      archived_paths: dict[str, str] | None = None) -> str:
        """导出台账（xlsx 或 csv）。

        Args:
            invoices: 发票记录列表。
            batch: 关联批次（提供 batchId / profileVersion）。
            archived_paths: invoiceId -> 归档路径映射。

        Returns:
            导出文件路径。
        """
        columns = list(self.config.columns or _DEFAULT_COLUMNS)
        batch_id = batch.id if batch else ""
        profile_version = batch.profileVersion if batch else 1
        archived_paths = archived_paths or {}

        rows: list[dict[str, Any]] = []
        for inv in invoices:
            row = inv.to_row()
            row["archivedPath"] = archived_paths.get(inv.id, "")
            row["batchId"] = batch_id
            row["profileVersion"] = profile_version
            review = inv.review_fields()
            row["fieldStatus"] = "OK" if not review else "REVIEW: " + ",".join(
                s.key for s in review
            )
            rows.append({col: row.get(col, "") for col in columns})

        fmt = (self.config.format or "xlsx").lower()
        stem = f"ledger_{batch_id or 'all'}"
        if fmt == "xlsx":
            try:
                return self._write_xlsx(rows, columns, self._ensure_dir() / f"{stem}.xlsx")
            except ImportError:
                self._logger.warning("%s: openpyxl 缺失，台账降级为 CSV",
                                     ERROR_CODES["E402"])
        return self._write_csv(rows, columns, self._ensure_dir() / f"{stem}.csv")

    # -------------------------------------------------------------- 报告
    def export_report(self, report: BatchReport) -> str:
        """导出批次报告为 CSV（汇总 + 异常摘要）。"""
        self._ensure_dir()
        path = self.output_dir / f"report_{report.batchId}.csv"
        data = report.to_dict()
        rows: list[dict[str, Any]] = []
        for key, value in data["summary"].items():
            rows.append({"项目": key, "值": value})
        rows.append({"项目": "configVersion", "值": data["configVersion"]})
        rows.append({"项目": "timeRange", "值": data["timeRange"]})
        for idx, anomaly in enumerate(data["anomalies"], start=1):
            rows.append({"项目": f"异常{idx}", "值": anomaly})
        self._write_csv(rows, ["项目", "值"], path)
        return str(path)

    # ---------------------------------------------------------- 待复核清单
    def export_review_list(self, items: Sequence[dict[str, Any]], batch_id: str = "") -> str:
        """导出待复核清单（含原因与定位）。"""
        self._ensure_dir()
        path = self.output_dir / f"review_{batch_id or 'all'}.csv"
        columns = ["batchId", "invoiceId", "fieldKey", "reason", "detail", "status"]
        rows: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            row.setdefault("batchId", batch_id)
            rows.append({col: row.get(col, "") for col in columns})
        self._write_csv(rows, columns, path)
        return str(path)

    # -------------------------------------------------------------- 写入器
    def _write_csv(self, rows: Sequence[dict[str, Any]], columns: Sequence[str],
                   path: str | Path) -> str:
        """写 CSV；按配置编码（UTF-8-BOM 默认）。"""
        encoding = self.config.encoding or "UTF-8-BOM"
        enc = "utf-8-sig" if encoding == "UTF-8-BOM" else (
            "gbk" if encoding == "GBK" else "utf-8"
        )
        with Path(path).open("w", encoding=enc, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return str(path)

    def _write_xlsx(self, rows: Sequence[dict[str, Any]], columns: Sequence[str],
                    path: str | Path) -> str:
        """写 Excel（openpyxl），金额列以元字符串写入。"""
        from openpyxl import Workbook  # type: ignore[import-not-found]
        from openpyxl.styles import Font  # type: ignore[import-not-found]

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "台账"
        sheet.append(list(columns))
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([row.get(col, "") for col in columns])
        for idx, col in enumerate(columns, start=1):
            width = max(12, min(40, max((len(str(r.get(col, ""))) for r in rows), default=12) + 2))
            sheet.column_dimensions[sheet.cell(row=1, column=idx).column_letter].width = width
        workbook.save(str(path))
        return str(path)


__all__ = ["ExportEngine"]
