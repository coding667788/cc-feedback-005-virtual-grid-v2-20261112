"""截图前重绘：普通扰动可恢复；持续不稳定时有限重试后明确失败。"""

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run_collector.py", *map(str, args)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )


def test_moderate_chaos_redraws_are_discarded_and_retried(site_server, make_config):
    config_path, _ = make_config(f"{site_server}/site/index.html?chaos=1")
    proc = _run_cli("collect", config_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_persistent_instability_fails_with_bounded_retries(site_server, make_config):
    # fastchaos 每 120ms 重绘，配合极小重试预算，应明确失败而不是挂死或产出半成品。
    config_path, tmp_path = make_config(
        f"{site_server}/site/index.html?fastchaos=1", max_window_retries=3
    )
    proc = _run_cli("collect", config_path)
    assert proc.returncode == 4, proc.stdout + proc.stderr
    assert "持续不稳定" in proc.stderr

    run_root = tmp_path / "runs"
    run_dir = next(run_root.glob("run_*"))
    import sqlite3

    conn = sqlite3.connect(run_dir / "collector.db")
    try:
        batch = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
        columns = [r[1] for r in conn.execute("PRAGMA table_info(batches)").fetchall()]
        status_idx = columns.index("status")
        assert batch[status_idx] == "failed"
        cell_count = conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
        assert cell_count < 250 * 12  # 半成品绝不能被标记为 complete
    finally:
        conn.close()
