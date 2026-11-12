"""完整采集端到端：250×12 全部单元格、截图证据、哈希与逐格答案核对。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.answer_key import COLUMNS, ROW_COUNT, expected_cell


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: int | str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "run_collector.py", *map(str, args)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )


def test_full_collection_matches_answer_key(site_server, make_config):
    config_path, tmp_path = make_config(f"{site_server}/site/index.html")
    proc = _run_cli("collect", config_path)
    assert proc.returncode == 0, proc.stderr + proc.stdout

    run_dir = Path(_extract_run_dir(proc.stdout))
    assert (run_dir / "viewer.html").exists()
    conn = sqlite3.connect(run_dir / "collector.db")
    conn.row_factory = sqlite3.Row
    try:
        batch = conn.execute("SELECT * FROM batches WHERE status='complete'").fetchone()
        assert batch is not None
        cells = conn.execute("SELECT * FROM cells ORDER BY record_id, col_index").fetchall()
        assert len(cells) == ROW_COUNT * 12

        seen = set()
        shots = {
            r["id"]: r for r in conn.execute("SELECT * FROM screenshots").fetchall()
        }
        import hashlib

        for cell in cells:
            key = (cell["record_id"], cell["col_index"])
            assert key not in seen
            seen.add(key)
            assert cell["full_path"] == COLUMNS[cell["col_index"]]["full_path"]
            assert cell["cell_text"] == expected_cell(
                batch["version"], cell["record_id"], cell["col_index"]
            )
            shot = shots[cell["screenshot_id"]]
            image_path = run_dir / shot["file_path"]
            assert image_path.exists()
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            assert digest == shot["sha256"]
            assert cell["view_seq"] == shot["view_seq"]
            assert 0 <= cell["region_x"] <= shot["width"] - cell["region_w"]
            assert 0 <= cell["region_y"] <= shot["height"] - cell["region_h"]
            assert cell["region_w"] > 0 and cell["region_h"] > 0

        assert len(seen) == ROW_COUNT * 12
        assert len(shots) > 0
    finally:
        conn.close()

    verify = _run_cli("verify", run_dir)
    assert verify.returncode == 0, verify.stderr + verify.stdout
    result = json.loads(verify.stdout)
    assert result["ok"] is True
    assert result["actual_cells"] == ROW_COUNT * 12


def _extract_run_dir(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("运行目录："):
            return line.split("：", 1)[1].strip()
    raise AssertionError(f"未找到运行目录输出：{stdout}")
