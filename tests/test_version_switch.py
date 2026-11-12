"""采集中切换数据版本：禁止新旧拼接，按策略失败或整体重采。"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.answer_key import expected_cell


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _switch_version_after_delay(url: str, delay: float, target: str) -> threading.Thread:
    def worker():
        time.sleep(delay)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("#vgViewport .vg-cell", timeout=15000)
            page.click(f"[data-version-btn='{target}']")
            page.wait_for_timeout(600)
            browser.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run_collector.py", *map(str, args)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )


def test_version_switch_fail_policy_no_mixing(site_server, make_config):
    url = f"{site_server}/site/index.html"
    config_path, tmp_path = make_config(url, version_policy="fail")
    switcher = _switch_version_after_delay(url, delay=4.0, target="v2")
    proc = _run_cli("collect", config_path)
    switcher.join(timeout=30)

    assert proc.returncode == 3, proc.stdout + proc.stderr
    run_dir = next((tmp_path / "runs").glob("run_*"))
    conn = sqlite3.connect(run_dir / "collector.db")
    conn.row_factory = sqlite3.Row
    try:
        batch = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
        assert batch["status"] == "failed"
        assert batch["version"] == "v1"
        # 已采到的半成品全部属于 v1，不允许出现 v2 数据。
        versions = {
            r["version"] for r in conn.execute("SELECT DISTINCT version FROM cells")
        }
        assert versions <= {"v1"}
    finally:
        conn.close()


def test_version_switch_restart_policy_recollects(site_server, make_config):
    url = f"{site_server}/site/index.html"
    config_path, tmp_path = make_config(url, version_policy="restart")
    switcher = _switch_version_after_delay(url, delay=4.0, target="v2")
    proc = _run_cli("collect", config_path)
    switcher.join(timeout=30)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    run_dir = next((tmp_path / "runs").glob("run_*"))
    conn = sqlite3.connect(run_dir / "collector.db")
    conn.row_factory = sqlite3.Row
    try:
        batches = conn.execute("SELECT * FROM batches ORDER BY id").fetchall()
        statuses = [b["status"] for b in batches]
        assert "aborted" in statuses and "complete" in statuses
        complete = conn.execute(
            "SELECT * FROM batches WHERE status='complete'"
        ).fetchone()
        assert complete["version"] == "v2"

        cells = conn.execute(
            "SELECT * FROM cells WHERE batch_id=? ORDER BY record_id, col_index",
            (complete["id"],),
        ).fetchall()
        assert len(cells) == 250 * 12
        for cell in cells[:20]:
            assert cell["version"] == "v2"
            assert cell["cell_text"] == expected_cell(
                "v2", cell["record_id"], cell["col_index"]
            )

        # 完成批次的单元格没有任何 v1 混入。
        mixed = conn.execute(
            "SELECT COUNT(*) FROM cells WHERE batch_id=? AND version != 'v2'",
            (complete["id"],),
        ).fetchone()[0]
        assert mixed == 0
    finally:
        conn.close()
