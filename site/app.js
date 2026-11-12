"use strict";

// 模拟站点：横纵双轴虚拟滚动表格，DOM 节点池复用。
// 数据由确定性 PRNG 生成，不依赖任何网络请求或外部附件。

var ROW_COUNT = 250;
var ROW_HEIGHT = 36;
var ID_WIDTH = 78;
var COL_WIDTH = 132;
var ROW_OVERSCAN = 2;
var COL_OVERSCAN = 1;

var GROUPS = [
  { parent: "销售中心", leaves: ["名称", "负责人", "状态"] },
  { parent: "交付中心", leaves: ["名称", "负责人", "状态"] },
  { parent: "供应链", leaves: ["名称", "负责人", "状态"] },
  { parent: "财务中心", leaves: ["名称", "负责人", "状态"] }
];

var OWNERS = ["林岚", "周衡", "陈墨", "赵启", "孙遥", "何瑾"];
var STATUSES = ["未开始", "进行中", "已暂停", "已完成", "已取消"];

function buildColumns() {
  var cols = [];
  GROUPS.forEach(function (group) {
    group.leaves.forEach(function (leaf) {
      cols.push({
        colIndex: cols.length,
        parent: group.parent,
        leaf: leaf,
        fullPath: group.parent + " / " + leaf
      });
    });
  });
  return cols;
}

var COLUMNS = buildColumns();

// 与 Python 端 tests/answer_key.py 完全一致的 mulberry32（32 位无符号运算）。
function mulberry32(seed) {
  var a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    var t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function cellSeed(versionInt, rowIndex, colIndex) {
  return (versionInt * 1000000 + rowIndex * 1000 + colIndex * 7 + 13) >>> 0;
}

function cellText(versionInt, rowIndex, col) {
  var rnd = mulberry32(cellSeed(versionInt, rowIndex, col.colIndex));
  var n = Math.floor(rnd() * 1000000);
  if (col.leaf === "名称") {
    return col.parent.slice(0, 2) + "-项目-" + n;
  }
  if (col.leaf === "负责人") {
    return OWNERS[n % OWNERS.length];
  }
  return STATUSES[n % STATUSES.length];
}

function buildMatrix(versionInt) {
  var matrix = [];
  for (var rowIndex = 0; rowIndex < ROW_COUNT; rowIndex++) {
    var row = [];
    for (var c = 0; c < COLUMNS.length; c++) {
      row.push(cellText(versionInt, rowIndex, COLUMNS[c]));
    }
    matrix.push(row);
  }
  return matrix;
}

var state = {
  version: "v1",
  loading: false,
  viewSeq: 0,
  matrix: buildMatrix(1),
  chaosTimer: null
};

// 前端私有数据：采集器禁止读取，仅站点自身与测试使用。
window.__VG_PRIVATE__ = {
  rowCount: ROW_COUNT,
  columns: COLUMNS,
  getMatrix: function () { return state.matrix; },
  cellText: function (version, rowIndex, colIndex) {
    return cellText(version === "v2" ? 2 : 1, rowIndex, COLUMNS[colIndex]);
  }
};

var viewport = document.getElementById("vgViewport");
var spacer = document.getElementById("vgSpacer");
var body = document.getElementById("vgBody");
var headerScroll = document.getElementById("vgHeaderScroll");
var headerInner = document.getElementById("vgHeaderInner");
var hGroupRow = document.getElementById("vgHGroupRow");
var hLeafRow = document.getElementById("vgHLeafRow");
var loadingEl = document.getElementById("vgLoading");
var metaEls = {};
document.querySelectorAll("#vgMeta [data-meta]").forEach(function (el) {
  metaEls[el.getAttribute("data-meta")] = el;
});

var totalWidth = ID_WIDTH + COLUMNS.length * COL_WIDTH;
spacer.style.width = totalWidth + "px";
spacer.style.height = ROW_COUNT * ROW_HEIGHT + "px";
headerInner.style.width = totalWidth - ID_WIDTH + "px";

function renderHeader() {
  var groupSpans = {};
  GROUPS.forEach(function (group) {
    var el = document.createElement("div");
    el.className = "vg-hgroup";
    el.style.flexBasis = group.leaves.length * COL_WIDTH + "px";
    el.style.width = group.leaves.length * COL_WIDTH + "px";
    el.setAttribute("data-parent", group.parent);
    el.textContent = group.parent;
    hGroupRow.appendChild(el);
    groupSpans[group.parent] = group.leaves.length;
  });
  COLUMNS.forEach(function (col) {
    var el = document.createElement("div");
    el.className = "vg-hleaf";
    el.setAttribute("data-col-index", String(col.colIndex));
    el.setAttribute("data-parent", col.parent);
    el.setAttribute("data-leaf", col.leaf);
    el.setAttribute("data-full-path", col.fullPath);
    el.textContent = col.leaf;
    hLeafRow.appendChild(el);
  });
}

var viewportWidth = viewport.clientWidth || 900;
var visibleRows = Math.ceil(viewport.clientHeight / ROW_HEIGHT);
var ROW_SLOTS = visibleRows + ROW_OVERSCAN * 2;
var CELL_SLOTS = Math.min(
  COLUMNS.length,
  Math.ceil((viewportWidth - ID_WIDTH) / COL_WIDTH) + COL_OVERSCAN * 2 + 1
);

var rowPool = [];

function buildRowPool() {
  for (var s = 0; s < ROW_SLOTS; s++) {
    var row = document.createElement("div");
    row.className = "vg-row";
    row.style.width = totalWidth + "px";
    row.setAttribute("data-row-slot", String(s));

    var idCell = document.createElement("div");
    idCell.className = "vg-cell vg-cell-id";
    row.appendChild(idCell);

    var cells = [];
    for (var k = 0; k < CELL_SLOTS; k++) {
      var cell = document.createElement("div");
      cell.className = "vg-cell";
      cell.style.position = "absolute";
      cell.style.width = COL_WIDTH + "px";
      row.appendChild(cell);
      cells.push(cell);
    }
    row._idCell = idCell;
    row._cells = cells;
    body.appendChild(row);
    rowPool.push(row);
  }
}

function blankCell(cell) {
  cell.removeAttribute("data-record-id");
  cell.removeAttribute("data-col-index");
  cell.removeAttribute("data-parent");
  cell.removeAttribute("data-leaf");
  cell.removeAttribute("data-full-path");
  cell.textContent = "";
  cell.style.display = "none";
  cell.style.left = "";
}

function bindCell(cell, rowIndex, col) {
  cell.style.display = "flex";
  cell.style.left = ID_WIDTH + col.colIndex * COL_WIDTH + "px";
  cell.setAttribute("data-record-id", String(rowIndex + 1));
  cell.setAttribute("data-col-index", String(col.colIndex));
  cell.setAttribute("data-parent", col.parent);
  cell.setAttribute("data-leaf", col.leaf);
  cell.setAttribute("data-full-path", col.fullPath);
  cell.textContent = state.matrix[rowIndex][col.colIndex];
}

function render() {
  state.viewSeq += 1;
  metaEls.viewSeq.textContent = String(state.viewSeq);
  metaEls.loading.textContent = state.loading ? "loading" : "ready";
  metaEls.recordCount.textContent = String(ROW_COUNT);
  metaEls.columnCount.textContent = String(COLUMNS.length);
  metaEls.version.textContent = state.version;
  loadingEl.hidden = !state.loading;

  var scrollTop = viewport.scrollTop;
  var scrollLeft = viewport.scrollLeft;
  headerScroll.scrollLeft = scrollLeft;

  var rowStart = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - ROW_OVERSCAN);
  var colStart = Math.max(
    0,
    Math.floor(scrollLeft / COL_WIDTH) - COL_OVERSCAN
  );
  var colEnd = Math.min(COLUMNS.length, colStart + CELL_SLOTS);

  for (var s = 0; s < rowPool.length; s++) {
    var row = rowPool[s];
    var rowIndex = rowStart + s;
    if (rowIndex >= ROW_COUNT || state.loading) {
      row.style.display = "none";
      row.removeAttribute("data-record-id");
      continue;
    }
    row.style.display = "flex";
    row.style.top = rowIndex * ROW_HEIGHT + "px";
    row.setAttribute("data-record-id", String(rowIndex + 1));
    row._idCell.textContent = "R" + String(rowIndex + 1).padStart(4, "0");
    row._idCell.setAttribute("data-record-id", String(rowIndex + 1));

    for (var k = 0; k < row._cells.length; k++) {
      var cell = row._cells[k];
      var colPos = colStart + k;
      if (colPos >= colEnd) {
        blankCell(cell);
      } else {
        bindCell(cell, rowIndex, COLUMNS[colPos]);
      }
    }
  }
}

var rafPending = false;
function scheduleRender() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(function () {
    rafPending = false;
    render();
  });
}

viewport.addEventListener("scroll", scheduleRender, { passive: true });
window.addEventListener("resize", scheduleRender);

function setVersion(nextVersion) {
  if (state.version === nextVersion || state.loading) return;
  state.loading = true;
  render();
  window.setTimeout(function () {
    state.version = nextVersion;
    state.matrix = buildMatrix(nextVersion === "v2" ? 2 : 1);
    state.loading = false;
    render();
  }, 350);
}

document.querySelectorAll("[data-version-btn]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    setVersion(btn.getAttribute("data-version-btn"));
  });
});

document.getElementById("vgRedrawBtn").addEventListener("click", function () {
  render();
});

var chaosBtn = document.getElementById("vgChaosBtn");
chaosBtn.addEventListener("click", function () {
  if (state.chaosTimer) {
    window.clearInterval(state.chaosTimer);
    state.chaosTimer = null;
    chaosBtn.textContent = "开启重绘扰动";
    chaosBtn.classList.remove("is-active");
  } else {
    var interval = /fastchaos=1/.test(location.search) ? 120 : 700;
    state.chaosTimer = window.setInterval(render, interval);
    chaosBtn.textContent = "停止重绘扰动";
    chaosBtn.classList.add("is-active");
  }
});

function syncActiveButton() {
  document.querySelectorAll("[data-version-btn]").forEach(function (btn) {
    btn.classList.toggle(
      "is-active",
      btn.getAttribute("data-version-btn") === state.version
    );
  });
}

var originalRender = render;
render = function () {
  originalRender();
  syncActiveButton();
};

// URL 参数：?version=v2 初始版本；?chaos=1 进入页面即开启扰动；?fastchaos=1 高频扰动。
(function initFromQuery() {
  var params = new URLSearchParams(location.search);
  if (params.get("version") === "v2") {
    state.version = "v2";
    state.matrix = buildMatrix(2);
  }
  renderHeader();
  buildRowPool();
  render();
  if (params.get("chaos") === "1" || params.get("fastchaos") === "1") {
    chaosBtn.click();
  }
})();
