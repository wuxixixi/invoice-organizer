"""端到端演示：用真实发票走完整整理流程（受控模式，不启动后台监控）。

只读用户原始发票（G:/own/发票），归档/导出写入临时目录，绝不改动原件。
用法：python demo_run.py [--keep]
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from invoice_organizer.config.schema import CategoryRule, MatchRule, ProfileConfig
from invoice_organizer.core.engine import OrganizeEngine
from invoice_organizer.core.exporter import ExportEngine
from invoice_organizer.core.watcher import StableFile
from invoice_organizer.storage.db import Database
from invoice_organizer.utils.hashutil import HashUtil
from invoice_organizer.utils.logging import configure_logging
from invoice_organizer.models.invoice import FIELD_KEYS

SOURCE = Path("G:/own/发票")
PROJECT_DATA = Path(__file__).resolve().parent / "data"  # 密钥所在（INVOICE_LLM_KEY 优先）

# 三张代表性真实发票
DEMO_FILES = [
    SOURCE / "发票" / "DMXAPI发票-300.00.pdf",       # 数电票·技术服务
    SOURCE / "发票" / "invoice_24957000000029580432.pdf",  # 数电票·信息服务
    SOURCE / "24933884.pdf",                          # 旧版增值税票·餐饮
]


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def main() -> int:
    # 日志与 banner 同走 stdout，避免重定向时 stderr 抢先输出造成顺序错觉
    configure_logging(PROJECT_DATA)
    root = Path(tempfile.mkdtemp(prefix="invoice_demo_"))
    inbox, archive = root / "inbox", root / "archive"
    quarantine = root / "quarantine"
    for p in (inbox, archive, quarantine):
        p.mkdir(parents=True, exist_ok=True)

    banner("步骤 1/8  投放真实发票到监控目录（只读复制原件）")
    staged = []
    for f in DEMO_FILES:
        if not f.exists():
            print(f"  跳过（不存在）：{f.name}")
            continue
        dst = inbox / f.name
        shutil.copy2(f, dst)
        sha = HashUtil.file_sha256(dst)
        staged.append((dst, sha))
        print(f"  {f.name}  {dst.stat().st_size} 字节  SHA256={sha[:16]}...")

    banner("步骤 2/8  配置档案（自定义分类 / 命名 / 目录 / 金额口径）")
    cfg = ProfileConfig.default()
    cfg.watch.path = str(inbox)
    cfg.output.archiveRoot = str(archive)
    cfg.output.quarantine = str(quarantine)
    cfg.categories = [
        CategoryRule(id="_tech", label="技术服务", priority=10,
                     match=MatchRule(field="itemName", contains="技术")),
        CategoryRule(id="info", label="信息服务", priority=20,
                     match=MatchRule(field="itemName", contains="信息")),
        CategoryRule(id="meal", label="餐饮", priority=30,
                     match=MatchRule(field="itemName", contains="餐饮")),
    ]
    cfg.directoryTemplate = "{issueDate}/{categoryLabel}"
    cfg.filenameTemplate = "{issueDate}_{sellerName}_{amountCent}_{invoiceNo}"
    cfg.amountSource = "totalExclTax"
    cfg.execution.previewRequired = True
    print(f"  输入目录：{inbox.name}/   归档目录：{archive.name}/")
    print(f"  金额口径：{cfg.amountSource}（合计不含税，默认）")
    print(f"  分类规则：{[(c.label, c.priority) for c in cfg.categories]}")
    print(f"  命名模板：{cfg.filenameTemplate}")
    print(f"  OCR：{cfg.ocr.model} @ {cfg.ocr.endpoint}（内网直连，无 Key）")
    print(f"  LLM：{cfg.llm.model}（enabled={cfg.llm.enabled}）")

    # 文件数据库（跨线程/多连接安全），密钥从项目 data/ 读取
    db = Database(str(root / "meta.db"))
    db.init_schema()
    engine = OrganizeEngine(cfg, db, data_dir=PROJECT_DATA)

    banner("步骤 3/8  启动批次")
    # 文件已在步骤 1 投放完毕：受控模式只创建批次，不启动后台监控，避免重复投递
    batch_id = engine.start("", start_watcher=False)
    print(f"  批次 ID：{batch_id}")

    banner("步骤 4/8  逐张：去重 → 解析 → 八字段提取 → 分类")
    for path, sha in staged:
        t0 = time.time()
        stable = StableFile(path=str(path), sha256=sha, size=path.stat().st_size,
                            mtimeNs=path.stat().st_mtime_ns)
        code = engine.on_stable_file(stable)
        print(f"\n  [{path.name}]  返回码={code}  耗时={time.time()-t0:.2f}s")

    invs = db.list_invoices(batch_id)
    print(f"\n  入表记录数：{len(invs)}")
    for inv in invs:
        print(f"\n  ── {inv.invoiceNo}  分类={inv.categoryLabel} ──")
        for key in FIELD_KEYS:
            slot = inv.get_field(key)
            val = slot.value if slot.value else "—"
            print(f"     {key:<13} = {str(val):<42} [{slot.confidence.value}]")

    reviews = db.list_review(batch_id)
    print(f"\n  待人工确认项：{len(reviews)}")
    for r in reviews[:10]:
        print(f"    - {r.get('field_key')}: {r.get('reason')}  {str(r.get('detail',''))[:44]}")

    banner("步骤 5/8  生成整理计划（旧路径 → 新路径，未确认不写盘）")
    plan = engine.build_plan(batch_id)
    print(f"  计划 ID：{plan.id}  状态：{plan.status}  计划项：{len(plan.items)}")
    for item in plan.items:
        print(f"\n    {Path(item.oldPath).name}")
        print(f"      → {item.newPath}")
        print(f"      命中规则={item.hitRuleId}  待复核={item.missingFields}")

    banner("步骤 6/8  人工确认计划")
    ok = engine.confirm_plan(plan.id, [])
    print(f"  确认结果：{ok}")

    banner("步骤 7/8  执行归档（原子写 / 不覆盖 / 哈希复核 / 原件只读）")
    report = engine.execute_batch(batch_id)
    s = report.summary
    print(f"  批次={report.batchId}  配置版本={report.configVersion}  时间范围={report.timeRange}")
    print(f"  输入={s.inputCount}  成功={s.successCount}  失败={s.failedCount}  "
          f"冲突={s.conflictCount}  重复投递={s.duplicateCount}  待复核={s.reviewCount}")
    for a in report.anomalies:
        print(f"  异常：{a}")
    # 归档后按命名模板改名，用计划项建立「新路径 → 源文件」映射做哈希复核
    src_by_dst: dict[Path, Path] = {}
    for item in plan.items:
        dst = Path(item.newPath)
        if not dst.is_absolute():
            dst = archive / dst
        src_by_dst[dst.resolve()] = Path(item.oldPath)
    archived = [p for p in archive.rglob("*") if p.is_file()]
    print(f"  归档文件数：{len(archived)}")
    for f in archived:
        rel = f.relative_to(archive)
        src = src_by_dst.get(f.resolve())
        if src is not None and src.exists():
            same = HashUtil.file_sha256(f) == HashUtil.file_sha256(src)
            verdict = "一致" if same else "不一致"
        else:
            verdict = "无法定位源文件"
        print(f"    {rel}  {f.stat().st_size} 字节  哈希复核={verdict}")
    print(f"  原件仍在监控目录（只读未删）：{all(p.exists() for p, _ in staged)}")

    banner("步骤 8/8  导出台账 Excel + CSV + 批次报告 + 待复核清单")
    from dataclasses import replace

    batch = db.get_batch(batch_id)
    archived_paths: dict[str, str] = {}
    for f in archived:
        for inv in invs:
            if inv.invoiceNo and inv.invoiceNo in f.name:
                archived_paths[inv.id] = str(f.relative_to(archive))
                break
    exporter = ExportEngine(cfg.export, root / "exports")
    xlsx = Path(ExportEngine(replace(cfg.export, format="xlsx"), root / "exports")
                .export_ledger(invs, batch, archived_paths))
    csv = Path(ExportEngine(replace(cfg.export, format="csv"), root / "exports")
               .export_ledger(invs, batch, archived_paths))
    report_csv = Path(exporter.export_report(report))
    review_csv = Path(exporter.export_review_list(reviews, batch_id))
    for label, p in (("Excel 台账", xlsx), ("CSV 台账", csv),
                     ("批次报告", report_csv), ("待复核清单", review_csv)):
        print(f"  {label}：{p.name}  {p.stat().st_size if p.exists() else '未生成'} 字节")
    print("\n  台账内容预览（CSV 前 6 行）：")
    if csv.exists():
        for line in csv.read_text(encoding="utf-8-sig").splitlines()[:6]:
            print("    " + line[:170])

    banner("演示完成")
    print(f"  产物目录：{root}")
    print("  用户原始发票 G:/own/发票 全程只读，未做任何改动")
    if "--keep" not in sys.argv:
        shutil.rmtree(root, ignore_errors=True)
        print("  临时目录已清理（加 --keep 可保留）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
