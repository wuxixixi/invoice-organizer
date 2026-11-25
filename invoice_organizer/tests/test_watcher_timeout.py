"""D2 回归：写稳超时回调上报 + 引擎落「待处理」待复核记录。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from invoice_organizer.config.schema import ProfileConfig, WatchConfig
from invoice_organizer.core.engine import OrganizeEngine
from invoice_organizer.core.watcher import FileWatcher
from invoice_organizer.storage.db import Database


def test_watcher_timeout_invokes_callback_with_e101(tmp_watch: Path) -> None:
    """注入 on_timeout 桩回调后，写稳超时必须回调且详情含 E101。"""
    pdf = tmp_watch / "slow.pdf"
    pdf.write_bytes(b"%PDF-1.4 slow writer")
    seen: list[str] = []
    # 极大 settleSeconds + 极小 maxSettleWait：直接驱动 _scan_once 避免真实长等待
    cfg = WatchConfig(path=str(tmp_watch), recursive=False,
                      settleSeconds=999.0, maxSettleWait=1)
    watcher = FileWatcher(cfg, on_timeout=seen.append)
    # 把该文件标记为“很早之前发现”，触发超时分支
    watcher._pending[str(pdf)] = time.monotonic() - 10_000

    watcher._scan_once(settle=0.01, max_wait=0.001)

    assert len(seen) == 1
    assert "E101" in seen[0]
    assert str(pdf) in seen[0]
    assert str(pdf) not in watcher._pending  # 超时项已移出 pending


def test_watcher_without_callback_keeps_legacy_behavior(tmp_watch: Path) -> None:
    """未注入回调时行为与原先一致：仅移出 pending，不抛异常。"""
    pdf = tmp_watch / "slow2.pdf"
    pdf.write_bytes(b"%PDF legacy")
    cfg = WatchConfig(path=str(tmp_watch), recursive=False,
                      settleSeconds=999.0, maxSettleWait=1)
    watcher = FileWatcher(cfg)
    watcher._pending[str(pdf)] = time.monotonic() - 10_000

    watcher._scan_once(settle=0.01, max_wait=0.001)

    assert str(pdf) not in watcher._pending
    assert watcher.queue.empty()


def test_engine_injects_timeout_review_into_db(tmp_watch: Path, tmp_archive: Path,
                                               tmp_path: Path,
                                               config: ProfileConfig,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """引擎注入的回调确实调用 db.add_review，落一条 E101 待处理记录。"""
    db = Database(tmp_path / "timeout.db")
    db.init_schema()
    engine = OrganizeEngine(config=config, db=db, data_dir=tmp_path / "data",
                            allow_network=False)
    calls: list[tuple[str, str, str, str, str]] = []
    original = db.add_review

    def spy(batch_id: str, invoice_id: str, field_key: str,
            reason: str, detail: str = "") -> None:
        calls.append((batch_id, invoice_id, field_key, reason, detail))
        original(batch_id, invoice_id, field_key, reason, detail)

    monkeypatch.setattr(db, "add_review", spy)

    batch_id = engine.start("", start_watcher=False)  # 仅建批次，不启动后台线程
    # 经 watcher 持有的回调入口触发，验证注入链路完整
    assert engine.watcher.on_timeout is not None
    engine.watcher.on_timeout("E101 写稳超时: /inbox/stuck.pdf")

    assert len(calls) == 1
    assert calls[0][:4] == (batch_id, "", "file", "E101")
    assert "E101" in calls[0][4]
    # 数据库可查：待复核清单含该条待处理记录
    rows = db.list_review(batch_id)
    assert any(r["reason"] == "E101" and "stuck.pdf" in r["detail"] for r in rows)
