"""采集主流程。

严格只使用页面公开信息：meta 文本、可见 DOM 单元格、滚动与点击、元素截图。
不读取 window 私有对象、不拦截网络响应、不读取测试答案。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .config import CollectorConfig
from .storage import RunStorage


class CollectionError(RuntimeError):
    pass


class UnstableError(CollectionError):
    pass


class VersionChangedError(CollectionError):
    pass


SNAPSHOT_JS = """
() => {
  const meta = {};
  document.querySelectorAll('#vgMeta [data-meta]').forEach((el) => {
    meta[el.getAttribute('data-meta')] = el.textContent.trim();
  });
  const vp = document.getElementById('vgViewport');
  const vpRect = vp.getBoundingClientRect();
  const cells = [];
  vp.querySelectorAll('.vg-cell[data-full-path]').forEach((el) => {
    if (el.offsetParent === null && el.style.display === 'none') return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    cells.push({
      recordId: Number(el.getAttribute('data-record-id')),
      colIndex: Number(el.getAttribute('data-col-index')),
      parent: el.getAttribute('data-parent'),
      leaf: el.getAttribute('data-leaf'),
      fullPath: el.getAttribute('data-full-path'),
      text: el.textContent,
      x: r.left - vpRect.left,
      y: r.top - vpRect.top,
      w: r.width,
      h: r.height
    });
  });
  return {
    meta,
    scrollTop: vp.scrollTop,
    scrollLeft: vp.scrollLeft,
    maxScrollTop: vp.scrollHeight - vp.clientHeight,
    maxScrollLeft: vp.scrollWidth - vp.clientWidth,
    clientWidth: vp.clientWidth,
    clientHeight: vp.clientHeight,
    cells
  };
}
"""

HEADER_JS = """
() => Array.from(document.querySelectorAll('.vg-hleaf[data-full-path]')).map((el) => ({
  colIndex: Number(el.getAttribute('data-col-index')),
  parent: el.getAttribute('data-parent'),
  leaf: el.getAttribute('data-leaf'),
  fullPath: el.getAttribute('data-full-path')
}))
"""


def _stable_cells_signature(snapshot: dict) -> list:
    return [
        (
            c["recordId"],
            c["colIndex"],
            c["text"],
            round(c["x"], 2),
            round(c["y"], 2),
        )
        for c in snapshot["cells"]
    ]


class TableCollector:
    def __init__(self, cfg: CollectorConfig, storage: RunStorage):
        self.cfg = cfg
        self.storage = storage
        self.batch_id: int | None = None
        self.version: str | None = None
        self.expected_rows = 0
        self.expected_cols = 0
        self.headers: list[dict] = []
        self.restarted_versions: list[str] = []

    def _new_batch(self, version: str, rows: int, cols: int) -> int:
        return self.storage.create_batch(
            version, rows, cols, json.dumps(self.cfg.to_dict(), ensure_ascii=False)
        )

    def run(self) -> dict:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.cfg.headless)
            page = browser.new_page(
                viewport={
                    "width": self.cfg.viewport_width,
                    "height": self.cfg.viewport_height,
                },
                device_scale_factor=1,
            )
            try:
                page.goto(self.cfg.url, wait_until="domcontentloaded")
                page.wait_for_selector("#vgViewport .vg-cell", timeout=15000)
                page.wait_for_timeout(300)
                # 点击一个可见单元格让表格获得焦点（仅使用公开交互）。
                page.locator("#vgViewport .vg-cell[data-full-path]").first.click()
                return self._collect_loop(page)
            finally:
                browser.close()

    def _read_meta(self, page) -> dict:
        snapshot = page.evaluate(SNAPSHOT_JS)
        meta = snapshot["meta"]
        required = {"recordCount", "columnCount", "version", "loading", "viewSeq"}
        missing = required - set(meta)
        if missing:
            raise CollectionError(f"页面未公开必需 meta：{sorted(missing)}")
        return snapshot

    def _wait_stable_snapshot(self, page):
        attempts = 0
        last = None
        while True:
            attempts += 1
            current = page.evaluate(SNAPSHOT_JS)
            if last is not None:
                same = (
                    current["meta"]["viewSeq"] == last["meta"]["viewSeq"]
                    and current["meta"]["loading"] == "ready"
                    and current["scrollTop"] == last["scrollTop"]
                    and current["scrollLeft"] == last["scrollLeft"]
                    and _stable_cells_signature(current)
                    == _stable_cells_signature(last)
                )
                if same:
                    return current
            last = current
            if attempts > self.cfg.max_window_retries:
                raise UnstableError("窗口持续重绘/滚动，无法获得稳定快照")
            page.wait_for_timeout(250)

    def _assert_version(self, snapshot: dict) -> None:
        current_version = snapshot["meta"]["version"]
        if current_version != self.version:
            raise VersionChangedError(
                f"采集期间数据版本由 {self.version} 变为 {current_version}"
            )
        if snapshot["meta"]["loading"] != "ready":
            raise UnstableError("页面处于加载状态")

    def _collect_window(self, page) -> list[dict]:
        for attempt in range(1, self.cfg.max_window_retries + 1):
            first = self._wait_stable_snapshot(page)
            self._assert_version(first)
            second = page.evaluate(SNAPSHOT_JS)
            if (
                second["meta"]["viewSeq"] != first["meta"]["viewSeq"]
                or second["meta"]["loading"] != "ready"
                or second["scrollTop"] != first["scrollTop"]
                or second["scrollLeft"] != first["scrollLeft"]
                or _stable_cells_signature(second)
                != _stable_cells_signature(first)
            ):
                page.wait_for_timeout(250)
                continue

            viewport = page.locator("#vgViewport")
            box = viewport.bounding_box()
            png = viewport.screenshot()
            third = page.evaluate(SNAPSHOT_JS)

            if (
                third["meta"]["viewSeq"] != first["meta"]["viewSeq"]
                or third["meta"]["version"] != self.version
                or third["scrollTop"] != first["scrollTop"]
                or third["scrollLeft"] != first["scrollLeft"]
                or _stable_cells_signature(third)
                != _stable_cells_signature(first)
            ):
                # 文字读取与截图之间发生重绘：丢弃本次观察，稍后重取。
                self.storage.log(
                    self.batch_id,
                    "WARN",
                    f"窗口重绘，丢弃观察并重试（第 {attempt} 次）",
                )
                page.wait_for_timeout(300)
                continue

            view_seq = int(first["meta"]["viewSeq"])
            screenshot_id, _, _ = self.storage.write_screenshot(
                self.batch_id,
                png,
                view_seq,
                int(round(box["width"])),
                int(round(box["height"])),
            )
            observations = []
            for cell in first["cells"]:
                observations.append(
                    {
                        "version": self.version,
                        "record_id": cell["recordId"],
                        "col_index": cell["colIndex"],
                        "parent": cell["parent"],
                        "leaf": cell["leaf"],
                        "full_path": cell["fullPath"],
                        "text": cell["text"],
                        "screenshot_id": screenshot_id,
                        "region": {
                            "x": int(round(cell["x"])),
                            "y": int(round(cell["y"])),
                            "w": int(round(cell["w"])),
                            "h": int(round(cell["h"])),
                        },
                        "view_seq": view_seq,
                    }
                )
            return observations
        raise UnstableError("窗口重试上限耗尽，采集失败")

    def _scroll_positions(self, span: int, client: int, fraction: float) -> list[int]:
        if span <= 0:
            return [0]
        step = max(1, int(client * fraction))
        positions = list(range(0, span, step))
        if positions[-1] != span:
            positions.append(span)
        return positions

    def _collect_loop(self, page) -> dict:
        snapshot = self._read_meta(page)
        self.version = snapshot["meta"]["version"]
        self.expected_rows = int(snapshot["meta"]["recordCount"])
        self.expected_cols = int(snapshot["meta"]["columnCount"])
        self.headers = page.evaluate(HEADER_JS)
        if len(self.headers) != self.expected_cols:
            raise CollectionError(
                f"公开表头列数 {len(self.headers)} 与 meta 列数 {self.expected_cols} 不一致"
            )

        existing = self.storage.get_running_batch()
        if existing is not None and existing["version"] == self.version:
            self.batch_id = int(existing["id"])
            self.storage.log(self.batch_id, "INFO", "续采已有 running 批次")
        else:
            self.batch_id = self._new_batch(
                self.version, self.expected_rows, self.expected_cols
            )

        total_expected = self.expected_rows * self.expected_cols
        known = self.storage.known_keys(self.batch_id)
        stall_windows = 0
        windows_done = 0

        while True:
            base = page.evaluate(SNAPSHOT_JS)
            y_positions = self._scroll_positions(
                base["maxScrollTop"],
                base["clientHeight"],
                self.cfg.row_step_fraction,
            )
            x_positions = self._scroll_positions(
                base["maxScrollLeft"],
                base["clientWidth"],
                self.cfg.col_step_fraction,
            )

            for y in y_positions:
                for x in x_positions:
                    page.evaluate(
                        """
                        ([top, left]) => {
                            const vp = document.getElementById('vgViewport');
                            vp.scrollTop = top;
                            vp.scrollLeft = left;
                        }
                        """,
                        [y, x],
                    )
                    page.wait_for_timeout(self.cfg.scroll_wait_ms)
                    try:
                        observations = self._collect_window(page)
                    except VersionChangedError:
                        return self._handle_version_change(page)
                    added = self.storage.upsert_cells(self.batch_id, observations)
                    before = len(known)
                    known.update(
                        (o["record_id"], o["col_index"]) for o in observations
                    )
                    windows_done += 1
                    stall_windows = stall_windows + 1 if len(known) == before else 0
                    if stall_windows >= self.cfg.max_stall_windows:
                        raise UnstableError(
                            f"连续 {stall_windows} 个窗口没有新单元格，判定无法采齐"
                        )

            if len(known) >= total_expected:
                self._assert_complete(known, total_expected)
                self.storage.mark_batch(self.batch_id, "complete")
                self.storage.log(self.batch_id, "INFO", "批次采集完成")
                return self._result(known)

            self.storage.log(
                self.batch_id,
                "INFO",
                f"首轮结束已采 {len(known)}/{total_expected}，继续回扫",
            )

    def _assert_complete(self, known: set[tuple[int, int]], total_expected: int) -> None:
        expected_keys = {
            (record_id, col.colIndex)
            for record_id in range(1, self.expected_rows + 1)
            for col in self.headers
        }
        missing = expected_keys - known
        extra = known - expected_keys
        if missing or extra:
            raise CollectionError(
                f"完成闸门校验失败：缺失 {len(missing)} 格，越界 {len(extra)} 格"
            )
        if len(known) != total_expected:
            raise CollectionError("单元格总数与期望不符，禁止把半成品当成果")

    def _handle_version_change(self, page) -> dict:
        old_batch = self.batch_id
        old_version = self.version
        if self.cfg.version_policy == "fail":
            self.storage.mark_batch(
                old_batch, "failed", f"采集中版本由 {old_version} 切换，拒绝拼接新旧数据"
            )
            raise VersionChangedError(
                f"采集中版本由 {old_version} 切换；当前策略 fail，批次标记 failed"
            )
        self.storage.mark_batch(
            old_batch, "aborted", f"版本切换为新版本，旧版本数据不拼接"
        )
        snapshot = self._wait_stable_snapshot(page)
        self.version = snapshot["meta"]["version"]
        self.expected_rows = int(snapshot["meta"]["recordCount"])
        self.expected_cols = int(snapshot["meta"]["columnCount"])
        self.restarted_versions.append(self.version)
        self.batch_id = self._new_batch(
            self.version, self.expected_rows, self.expected_cols
        )
        self.storage.log(self.batch_id, "INFO", f"版本切换，重新采集新版本 {self.version}")
        return self._collect_loop(page)

    def _result(self, known: set[tuple[int, int]]) -> dict:
        return {
            "batch_id": self.batch_id,
            "version": self.version,
            "rows": self.expected_rows,
            "cols": self.expected_cols,
            "cells": len(known),
            "restarted_versions": self.restarted_versions,
        }
