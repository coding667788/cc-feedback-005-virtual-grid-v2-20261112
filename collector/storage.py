"""SQLite 持久化、截图哈希登记、中断恢复与完整性校验。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_dir TEXT NOT NULL,
    version TEXT NOT NULL,
    expected_rows INTEGER NOT NULL,
    expected_cols INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    config_json TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    view_seq INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, sha256)
);

CREATE TABLE IF NOT EXISTS cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    col_index INTEGER NOT NULL,
    parent_name TEXT NOT NULL,
    leaf_name TEXT NOT NULL,
    full_path TEXT NOT NULL,
    cell_text TEXT NOT NULL,
    screenshot_id INTEGER NOT NULL,
    region_x INTEGER NOT NULL,
    region_y INTEGER NOT NULL,
    region_w INTEGER NOT NULL,
    region_h INTEGER NOT NULL,
    view_seq INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, version, record_id, col_index),
    FOREIGN KEY(batch_id) REFERENCES batches(id),
    FOREIGN KEY(screenshot_id) REFERENCES screenshots(id)
);

CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    batch_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(batch_id, key)
);
"""


class IntegrityError(RuntimeError):
    pass


class RunStorage:
    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.evidence_dir = self.run_dir / "evidence"
        self.orphan_dir = self.evidence_dir / "orphans"
        self.db_path = self.run_dir / "collector.db"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.orphan_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def log(self, batch_id: int | None, level: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO run_log(batch_id, ts, level, message) VALUES (?,?,?,?)",
            (batch_id, datetime.utcnow().isoformat(timespec="seconds"), level, message),
        )
        self.conn.commit()

    def create_batch(
        self, version: str, expected_rows: int, expected_cols: int, config_json: str
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO batches(run_dir, version, expected_rows, expected_cols,
                                status, started_at, config_json)
            VALUES (?,?,?,?,'running',?,?)
            """,
            (
                str(self.run_dir),
                version,
                expected_rows,
                expected_cols,
                datetime.utcnow().isoformat(timespec="seconds"),
                config_json,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_running_batch(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM batches WHERE status='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def get_latest_batch(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM batches ORDER BY id DESC LIMIT 1"
        ).fetchone()

    def mark_batch(self, batch_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE batches SET status=?, finished_at=?, error=? WHERE id=?",
            (
                status,
                datetime.utcnow().isoformat(timespec="seconds"),
                error,
                batch_id,
            ),
        )
        self.conn.commit()

    def checkpoint_get(self, batch_id: int, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM checkpoints WHERE batch_id=? AND key=?",
            (batch_id, key),
        ).fetchone()
        return row["value"] if row else None

    def checkpoint_set(self, batch_id: int, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO checkpoints(batch_id, key, value) VALUES (?,?,?)
            ON CONFLICT(batch_id, key) DO UPDATE SET value=excluded.value
            """,
            (batch_id, key, value),
        )
        self.conn.commit()

    @staticmethod
    def sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def write_screenshot(
        self,
        batch_id: int,
        png_bytes: bytes,
        view_seq: int,
        width: int,
        height: int,
    ) -> tuple[int, Path, str]:
        """先落盘再登记数据库；引用单元格必须在后续事务中写入。

        若在文件写入后、数据库提交前进程被杀，会留下半成品，
        由 recover_running_batch 在续采时检出并清理。
        """
        sha = hashlib.sha256(png_bytes).hexdigest()
        existing = self.conn.execute(
            "SELECT id, file_path FROM screenshots WHERE batch_id=? AND sha256=?",
            (batch_id, sha),
        ).fetchone()
        if existing:
            return int(existing["id"]), Path(existing["file_path"]), sha

        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
        rel_path = Path("evidence") / f"vseq{view_seq}_{stamp}_{sha[:10]}.png"
        abs_path = self.run_dir / rel_path
        abs_path.write_bytes(png_bytes)

        # 故障注入：图片已写入，但登记事务与单元格事务尚未提交。
        fault = os.environ.get("COLLECTOR_FAULT", "")
        if fault.startswith("crash_after_write:"):
            limit = int(fault.split(":", 1)[1])
            if len(list(self.evidence_dir.glob("*.png"))) >= limit:
                os._exit(2)

        cur = self.conn.execute(
            """
            INSERT INTO screenshots(batch_id, file_path, sha256, width, height,
                                    view_seq, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                batch_id,
                str(rel_path).replace("\\", "/"),
                sha,
                width,
                height,
                view_seq,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid), abs_path, sha

    def upsert_cells(self, batch_id: int, observations: list[dict]) -> int:
        now = datetime.utcnow().isoformat(timespec="seconds")
        inserted = 0
        with self.transaction():
            for obs in observations:
                exists = self.conn.execute(
                    "SELECT 1 FROM cells WHERE batch_id=? AND record_id=? AND col_index=?",
                    (batch_id, obs["record_id"], obs["col_index"]),
                ).fetchone()
                if exists:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO cells(batch_id, version, record_id, col_index,
                                      parent_name, leaf_name, full_path, cell_text,
                                      screenshot_id, region_x, region_y, region_w,
                                      region_h, view_seq, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        batch_id,
                        obs["version"],
                        obs["record_id"],
                        obs["col_index"],
                        obs["parent"],
                        obs["leaf"],
                        obs["full_path"],
                        obs["text"],
                        obs["screenshot_id"],
                        obs["region"]["x"],
                        obs["region"]["y"],
                        obs["region"]["w"],
                        obs["region"]["h"],
                        obs["view_seq"],
                        now,
                    ),
                )
                inserted += 1
        return inserted

    def cell_count(self, batch_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM cells WHERE batch_id=?", (batch_id,)
        ).fetchone()
        return int(row["n"])

    def known_keys(self, batch_id: int) -> set[tuple[int, int]]:
        rows = self.conn.execute(
            "SELECT record_id, col_index FROM cells WHERE batch_id=?", (batch_id,)
        ).fetchall()
        return {(int(r["record_id"]), int(r["col_index"])) for r in rows}

    def recover_running_batch(self) -> dict:
        """清理半成品：失效截图及其引用、已落盘未登记的孤儿图片。"""
        batch = self.get_running_batch()
        report = {"removed_screenshots": 0, "removed_cells": 0, "orphan_files": 0}
        if batch is None:
            return report
        batch_id = int(batch["id"])

        valid_ids: set[int] = set()
        for shot in self.conn.execute(
            "SELECT * FROM screenshots WHERE batch_id=?", (batch_id,)
        ).fetchall():
            path = self.run_dir / shot["file_path"]
            if not path.exists() or self.sha256_file(path) != shot["sha256"]:
                self.conn.execute(
                    "DELETE FROM cells WHERE screenshot_id=?", (shot["id"],)
                )
                self.conn.execute(
                    "DELETE FROM screenshots WHERE id=?", (shot["id"],)
                )
                report["removed_screenshots"] += 1
                if path.exists():
                    path.unlink()
            else:
                valid_ids.add(int(shot["id"]))

        placeholders = ",".join("?" for _ in valid_ids)
        if valid_ids:
            dangling = self.conn.execute(
                f"SELECT id FROM cells WHERE batch_id=? AND screenshot_id NOT IN ({placeholders})",
                (batch_id, *valid_ids),
            ).fetchall()
        else:
            dangling = self.conn.execute(
                "SELECT id FROM cells WHERE batch_id=?", (batch_id,)
            ).fetchall()
        if dangling:
            ids = [int(r["id"]) for r in dangling]
            self.conn.execute(
                "DELETE FROM cells WHERE id IN (%s)"
                % ",".join(str(i) for i in ids)
            )
            report["removed_cells"] += len(ids)

        registered = {
            Path(r["file_path"]).name
            for r in self.conn.execute(
                "SELECT file_path FROM screenshots WHERE batch_id=?", (batch_id,)
            ).fetchall()
        }
        for png in self.evidence_dir.glob("*.png"):
            if png.name not in registered:
                target = self.orphan_dir / png.name
                if target.exists():
                    target = self.orphan_dir / f"{time.time_ns()}_{png.name}"
                png.replace(target)
                report["orphan_files"] += 1

        self.conn.commit()
        self.log(batch_id, "INFO", f"中断恢复完成：{report}")
        return report

    def verify_batch(self, batch_id: int) -> dict:
        batch = self.conn.execute(
            "SELECT * FROM batches WHERE id=?", (batch_id,)
        ).fetchone()
        if batch is None:
            raise IntegrityError(f"批次不存在：{batch_id}")
        problems: list[str] = []
        cells = self.conn.execute(
            "SELECT * FROM cells WHERE batch_id=?", (batch_id,)
        ).fetchall()
        expected = int(batch["expected_rows"]) * int(batch["expected_cols"])
        if len(cells) != expected:
            problems.append(f"单元格数量 {len(cells)} != 期望 {expected}")

        keys = {(c["record_id"], c["col_index"]) for c in cells}
        if len(keys) != len(cells):
            problems.append("存在重复单元格键")

        shots = {
            int(r["id"]): r
            for r in self.conn.execute(
                "SELECT * FROM screenshots WHERE batch_id=?", (batch_id,)
            ).fetchall()
        }
        for cell in cells:
            shot = shots.get(int(cell["screenshot_id"]))
            if shot is None:
                problems.append(
                    f"单元格 {cell['record_id']}/{cell['col_index']} 缺少截图记录"
                )
                continue
            path = self.run_dir / shot["file_path"]
            if not path.exists():
                problems.append(f"截图缺失：{shot['file_path']}")
                continue
            if self.sha256_file(path) != shot["sha256"]:
                problems.append(f"截图被篡改：{shot['file_path']}")
            region_ok = (
                cell["region_w"] > 0
                and cell["region_h"] > 0
                and cell["region_x"] >= 0
                and cell["region_y"] >= 0
                and cell["region_x"] + cell["region_w"] <= shot["width"]
                and cell["region_y"] + cell["region_h"] <= shot["height"]
            )
            if not region_ok:
                problems.append(
                    f"像素区域越界：单元格 {cell['record_id']}/{cell['col_index']}"
                )
            if cell["view_seq"] != shot["view_seq"]:
                problems.append(
                    f"视图序号不一致：单元格 {cell['record_id']}/{cell['col_index']}"
                )

        return {
            "batch_id": batch_id,
            "status": batch["status"],
            "version": batch["version"],
            "expected_cells": expected,
            "actual_cells": len(cells),
            "problems": problems,
            "ok": not problems,
        }

    def export_cells(self, batch_id: int) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT c.*, s.file_path AS screenshot_file, s.sha256
            FROM cells c JOIN screenshots s ON s.id = c.screenshot_id
            WHERE c.batch_id=?
            ORDER BY c.record_id, c.col_index
            """,
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]
