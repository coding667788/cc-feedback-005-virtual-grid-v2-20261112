"""图片已写入、数据库未提交时中断：续采清理半成品并最终采齐；篡改/缺失可检出。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "run_collector.py", *map(str, args)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        env=env,
    )


def test_crash_between_file_write_and_commit_then_resume(site_server, make_config):
    config_path, tmp_path = make_config(f"{site_server}/site/index.html")

    crashed = _run_cli(
        "collect", config_path, env_extra={"COLLECTOR_FAULT": "crash_after_write:3"}
    )
    assert crashed.returncode == 2

    run_dir = next((tmp_path / "runs").glob("run_*"))
    evidence = run_dir / "evidence"
    pngs_before = list(evidence.glob("*.png"))
    assert pngs_before, "故障点前应有已落盘图片"

    # 半成品不能被当作完成结果。
    conn = sqlite3.connect(run_dir / "collector.db")
    try:
        status = conn.execute(
            "SELECT status FROM batches ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert status == "running"
    finally:
        conn.close()

    resumed = _run_cli("resume", run_dir, config_path)
    assert resumed.returncode == 0, resumed.stderr + resumed.stdout

    conn = sqlite3.connect(run_dir / "collector.db")
    conn.row_factory = sqlite3.Row
    try:
        batch = conn.execute(
            "SELECT * FROM batches WHERE status='complete'"
        ).fetchone()
        assert batch is not None
        assert conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 250 * 12

        # 所有被引用截图都真实存在且哈希一致。
        for shot in conn.execute("SELECT * FROM screenshots").fetchall():
            path = run_dir / shot["file_path"]
            assert path.exists()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == shot["sha256"]
    finally:
        conn.close()

    # 未登记的孤儿图片被移动到 orphans，不会静默留在证据目录。
    orphaned = list((evidence / "orphans").glob("*.png"))
    assert orphaned, "中断时未提交的图片应被识别为孤儿并隔离"

    verify = _run_cli("verify", run_dir)
    assert verify.returncode == 0, verify.stderr + verify.stdout


def test_tampered_and_missing_screenshots_are_detected(site_server, make_config):
    config_path, tmp_path = make_config(f"{site_server}/site/index.html")
    proc = _run_cli("collect", config_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    run_dir = next((tmp_path / "runs").glob("run_*"))

    conn = sqlite3.connect(run_dir / "collector.db")
    conn.row_factory = sqlite3.Row
    try:
        shots = conn.execute("SELECT * FROM screenshots ORDER BY id").fetchall()
        tampered = run_dir / shots[0]["file_path"]
        missing = run_dir / shots[1]["file_path"]
    finally:
        conn.close()

    tampered.write_bytes(b"not-a-real-png" + tampered.read_bytes()[:32])
    missing.unlink()

    verify = _run_cli("verify", run_dir)
    assert verify.returncode == 7
    import json

    result = json.loads(verify.stdout)
    messages = " ".join(result["problems"])
    assert "篡改" in messages
    assert "缺失" in messages


def test_complete_batch_is_not_destroyed_by_new_run(site_server, make_config):
    config_path, tmp_path = make_config(f"{site_server}/site/index.html")
    first = _run_cli("collect", config_path)
    assert first.returncode == 0
    first_dir = next((tmp_path / "runs").glob("run_*"))

    second = _run_cli("collect", config_path)
    assert second.returncode == 0
    run_dirs = sorted((tmp_path / "runs").glob("run_*"))
    assert len(run_dirs) == 2
    assert first_dir in run_dirs

    conn = sqlite3.connect(first_dir / "collector.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM cells"
        ).fetchone()[0] == 250 * 12
        assert conn.execute(
            "SELECT status FROM batches ORDER BY id DESC LIMIT 1"
        ).fetchone()[0] == "complete"
    finally:
        conn.close()
