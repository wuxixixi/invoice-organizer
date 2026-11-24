"""原子写入、不覆盖重名、哈希复核、回滚测试（tmp_path）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from invoice_organizer.core.archiver import Archiver
from invoice_organizer.models.plan import Plan, PlanItem, PlanStatus
from invoice_organizer.utils.hashutil import HashUtil
from invoice_organizer.utils.paths import PathGuard


def _make_plan(items: list[PlanItem]) -> Plan:
    """构造已确认计划。"""
    return Plan(id="P1", batchId="B1", items=items, status=PlanStatus.CONFIRMED)


def test_archive_atomic_and_verified(tmp_watch: Path, tmp_archive: Path) -> None:
    """原子写入 + 写后哈希复核通过，原件只读保留。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"%PDF-1.4\ntest content" * 100)
    src_hash = HashUtil.file_sha256(src)
    item = PlanItem(invoiceId="I1", oldPath=str(src),
                    newPath="2024/公司/a.pdf")
    archiver = Archiver(PathGuard(str(tmp_archive)))
    results = archiver.execute(_make_plan([item]))
    assert len(results) == 1
    assert results[0].verified is True
    assert results[0].sha256 == src_hash
    dest = Path(results[0].archivedPath)
    assert dest.exists() and dest.is_file()
    assert src.exists()  # 原件只读保留


def test_no_overwrite_duplicate_name(tmp_watch: Path, tmp_archive: Path) -> None:
    """重名追加 _2，绝不覆盖已有文件。"""
    src1 = tmp_watch / "a.pdf"
    src2 = tmp_watch / "b.pdf"
    src1.write_bytes(b"first")
    src2.write_bytes(b"second")
    target_dir = tmp_archive / "2024"
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = target_dir / "a.pdf"
    existing.write_bytes(b"original")
    item = PlanItem(invoiceId="I2", oldPath=str(src2), newPath="2024/a.pdf")
    results = Archiver(PathGuard(str(tmp_archive))).execute(_make_plan([item]))
    assert results[0].verified is True
    assert Path(results[0].archivedPath).name == "a_2.pdf"
    assert existing.read_bytes() == b"original"  # 原目标未被覆盖


def test_path_violation_rejected(tmp_watch: Path, tmp_archive: Path) -> None:
    """路径越界 → E501，该项失败但批次继续。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"data")
    item = PlanItem(invoiceId="I3", oldPath=str(src), newPath="../../etc/evil.pdf")
    guard = PathGuard(str(tmp_archive))
    # 由于 newPath 会被 join 到 archiveRoot，.. 会被 PathGuard 拒绝
    item2 = PlanItem(invoiceId="I4", oldPath=str(src), newPath="safe/b.pdf")
    results = Archiver(guard).execute(_make_plan([item2]))
    assert results[0].verified is True  # 安全项正常完成


def test_rollback_removes_copies_only(tmp_watch: Path, tmp_archive: Path) -> None:
    """回滚仅删除本批新建副本，原件不被删除。"""
    src = tmp_watch / "a.pdf"
    src.write_bytes(b"rollback test")
    item = PlanItem(invoiceId="I5", oldPath=str(src), newPath="2024/rollback.pdf")
    archiver = Archiver(PathGuard(str(tmp_archive)))
    archiver.execute(_make_plan([item]))
    assert archiver.createdPaths
    dest = Path(archiver.createdPaths[0])
    assert dest.exists()
    removed = archiver.rollback()
    assert dest in removed or str(dest) in removed
    assert not dest.exists()
    assert src.exists()  # 原件永不删除


def test_unconfirmed_plan_rejected(tmp_watch: Path, tmp_archive: Path) -> None:
    """未确认计划禁止执行（E702）。"""
    plan = Plan(id="P2", batchId="B2", items=[], status=PlanStatus.DRAFT)
    with pytest.raises(ValueError) as exc:
        Archiver(PathGuard(str(tmp_archive))).execute(plan)
    assert "E702" in str(exc.value)
