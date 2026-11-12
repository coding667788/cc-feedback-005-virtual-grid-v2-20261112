"""生成离线证据查看页（file:// 直接打开，无需服务器、无外部依赖）。"""

from __future__ import annotations

import json
from pathlib import Path

from .storage import RunStorage


VIEWER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>采集证据离线查看页</title>
  <link rel="stylesheet" href="viewer-app.css" />
</head>
<body>
  <header class="topbar">
    <h1>动态虚拟表格采集证据</h1>
    <div id="summary" class="summary"></div>
  </header>
  <section class="filters">
    <input id="qRecord" type="number" min="1" placeholder="记录编号，如 12" />
    <input id="qPath" type="text" placeholder="完整表头路径关键字，如 供应链 / 状态" />
    <button id="btnClear" type="button">清空筛选</button>
    <span id="filterCount" class="filter-count"></span>
  </section>
  <main class="layout">
    <div class="table-wrap">
      <table id="cellTable">
        <thead>
          <tr>
            <th>记录编号</th><th>完整表头路径</th><th>原文</th>
            <th>视图序号</th><th>图片摘要(sha256 前 12 位)</th><th>证据状态</th>
          </tr>
        </thead>
        <tbody id="cellBody"></tbody>
      </table>
    </div>
    <aside class="evidence">
      <h2>截图证据与像素区域高亮</h2>
      <div id="emptyTip">点击左侧任意单元格查看证据。</div>
      <div id="detail" hidden>
        <p id="dTitle" class="d-title"></p>
        <p id="dMeta" class="d-meta"></p>
        <div class="img-frame" id="imgFrame">
          <img id="dImage" alt="证据截图" />
          <div id="dHighlight" class="highlight" hidden></div>
        </div>
        <p class="d-path">图片路径：<span id="dPath"></span></p>
        <p class="d-sha">完整 sha256：<span id="dSha"></span></p>
        <p class="d-region">像素区域：<span id="dRegion"></span></p>
      </div>
    </aside>
  </main>
  <script src="viewer-data.js"></script>
  <script src="viewer-app.js"></script>
</body>
</html>
"""

VIEWER_CSS = """
* { box-sizing: border-box; }
body {
  margin: 0; font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
  font-size: 13px; color: #1f2733; background: #f4f6fa;
}
.topbar {
  display: flex; align-items: baseline; gap: 24px;
  padding: 14px 20px; background: #173a73; color: #fff;
}
.topbar h1 { font-size: 18px; margin: 0; }
.summary { font-size: 13px; opacity: .92; }
.filters {
  display: flex; gap: 10px; align-items: center;
  padding: 10px 20px; background: #fff; border-bottom: 1px solid #d9deea;
}
.filters input {
  padding: 6px 10px; border: 1px solid #c8cfdd; border-radius: 6px; font-size: 13px;
}
.filters button {
  padding: 6px 14px; border: 1px solid #2f6fdd; background: #2f6fdd;
  color: #fff; border-radius: 6px; cursor: pointer;
}
.filter-count { color: #5b6678; }
.layout {
  display: grid; grid-template-columns: minmax(420px, 1.1fr) minmax(420px, 1fr);
  gap: 14px; padding: 14px 20px;
}
.table-wrap {
  max-height: 72vh; overflow: auto; background: #fff;
  border: 1px solid #d9deea; border-radius: 8px;
}
table { border-collapse: collapse; width: 100%; }
th, td {
  padding: 7px 10px; border-bottom: 1px solid #eef1f6;
  text-align: left; white-space: nowrap;
}
th { position: sticky; top: 0; background: #eef2f8; z-index: 1; }
tbody tr { cursor: pointer; }
tbody tr:hover { background: #eef4ff; }
tbody tr.selected { background: #d9e7ff; }
.evidence {
  background: #fff; border: 1px solid #d9deea; border-radius: 8px;
  padding: 14px; max-height: 72vh; overflow: auto;
}
.evidence h2 { font-size: 15px; margin: 0 0 10px; }
.d-title { font-weight: 700; margin: 4px 0; }
.d-meta { color: #5b6678; margin: 4px 0 10px; }
.img-frame {
  position: relative; display: inline-block;
  max-width: 100%; border: 1px solid #d9deea; border-radius: 6px; overflow: hidden;
}
.img-frame img { display: block; max-width: 100%; }
.highlight {
  position: absolute; border: 3px solid #ff3b30;
  background: rgba(255, 59, 48, .14);
  pointer-events: none; box-shadow: 0 0 0 1px rgba(255,255,255,.8) inset;
}
.d-path, .d-sha, .d-region {
  word-break: break-all; color: #465061; margin: 8px 0 0;
}
.ok { color: #1a7f37; font-weight: 700; }
.bad { color: #c0392b; font-weight: 700; }
"""

VIEWER_JS = """
"use strict";
const data = window.VIEWER_DATA;
const body = document.getElementById("cellBody");
const qRecord = document.getElementById("qRecord");
const qPath = document.getElementById("qPath");
const filterCount = document.getElementById("filterCount");

document.getElementById("summary").textContent =
  `批次 ${data.batch_id} · 版本 ${data.version} · ${data.rows} 行 × ${data.cols} 列 · ` +
  `共 ${data.cells.length} 格证据 · 状态 ${data.status}`;

function renderRows() {
  const rec = qRecord.value.trim();
  const path = qPath.value.trim().toLowerCase();
  const rows = data.cells.filter((c) => {
    const recOk = !rec || String(c.record_id) === rec;
    const pathOk = !path || c.full_path.toLowerCase().includes(path);
    return recOk && pathOk;
  });
  filterCount.textContent = `匹配 ${rows.length} / ${data.cells.length} 格`;
  body.innerHTML = "";
  const frag = document.createDocumentFragment();
  rows.forEach((c) => {
    const tr = document.createElement("tr");
    tr.dataset.id = String(c.id);
    tr.innerHTML =
      `<td>R${String(c.record_id).padStart(4, "0")}</td>` +
      `<td>${c.full_path}</td>` +
      `<td>${escapeHtml(c.cell_text)}</td>` +
      `<td>${c.view_seq}</td>` +
      `<td>${c.sha256.slice(0, 12)}</td>` +
      `<td class="ok" data-evidence-state>待加载</td>`;
    tr.addEventListener("click", () => selectCell(c, tr));
    frag.appendChild(tr);
  });
  body.appendChild(frag);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function selectCell(c, tr) {
  document.querySelectorAll("tbody tr.selected").forEach((el) => el.classList.remove("selected"));
  tr.classList.add("selected");
  document.getElementById("emptyTip").hidden = true;
  const detail = document.getElementById("detail");
  detail.hidden = false;
  document.getElementById("dTitle").textContent =
    `R${String(c.record_id).padStart(4, "0")} · ${c.full_path}`;
  document.getElementById("dMeta").textContent =
    `原文：${c.cell_text}　|　视图序号：${c.view_seq}`;
  document.getElementById("dPath").textContent = c.screenshot_file;
  document.getElementById("dSha").textContent = c.sha256;
  document.getElementById("dRegion").textContent =
    `x=${c.region_x}, y=${c.region_y}, w=${c.region_w}, h=${c.region_h}`;
  const img = document.getElementById("dImage");
  const hl = document.getElementById("dHighlight");
  const stateCell = tr.querySelector("[data-evidence-state]");
  img.onload = () => {
    stateCell.textContent = "图片可离线打开";
    stateCell.className = "ok";
    hl.hidden = false;
    const scaleX = img.clientWidth / img.naturalWidth;
    const scaleY = img.clientHeight / img.naturalHeight;
    hl.style.left = `${c.region_x * scaleX}px`;
    hl.style.top = `${c.region_y * scaleY}px`;
    hl.style.width = `${c.region_w * scaleX}px`;
    hl.style.height = `${c.region_h * scaleY}px`;
  };
  img.onerror = () => {
    stateCell.textContent = "图片缺失或被修改后无法读取";
    stateCell.className = "bad";
    hl.hidden = true;
  };
  img.src = c.screenshot_file;
}

qRecord.addEventListener("input", renderRows);
qPath.addEventListener("input", renderRows);
document.getElementById("btnClear").addEventListener("click", () => {
  qRecord.value = "";
  qPath.value = "";
  renderRows();
});
renderRows();
"""


def build_viewer(run_dir: str | Path, batch_id: int | None = None) -> Path:
    run_dir = Path(run_dir)
    storage = RunStorage(run_dir)
    try:
        batch = (
            storage.conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch_id is not None
            else storage.get_latest_batch()
        )
        if batch is None:
            raise FileNotFoundError(f"{run_dir} 中没有批次数据")
        bid = int(batch["id"])
        cells = storage.export_cells(bid)
        payload = {
            "batch_id": bid,
            "version": batch["version"],
            "status": batch["status"],
            "rows": int(batch["expected_rows"]),
            "cols": int(batch["expected_cols"]),
            "cells": cells,
        }
        (run_dir / "viewer.html").write_text(VIEWER_HTML, encoding="utf-8")
        (run_dir / "viewer-app.css").write_text(VIEWER_CSS, encoding="utf-8")
        (run_dir / "viewer-app.js").write_text(VIEWER_JS, encoding="utf-8")
        data_js = (
            "window.VIEWER_DATA = "
            + json.dumps(payload, ensure_ascii=False, indent=2)
            + ";\n"
        )
        (run_dir / "viewer-data.js").write_text(data_js, encoding="utf-8")
        return run_dir / "viewer.html"
    finally:
        storage.close()
