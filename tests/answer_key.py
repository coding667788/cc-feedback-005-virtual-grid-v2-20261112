"""测试专用已知答案（仅测试读取，采集器源码禁止引用本模块）。

与 site/app.js 的 mulberry32 与 cellText 逐位对齐，用 Python 独立实现，
不共享前端私有数据，从而可以逐格核对采集结果。
"""

from __future__ import annotations

ROW_COUNT = 250

GROUPS = [
    ("销售中心", ["名称", "负责人", "状态"]),
    ("交付中心", ["名称", "负责人", "状态"]),
    ("供应链", ["名称", "负责人", "状态"]),
    ("财务中心", ["名称", "负责人", "状态"]),
]

OWNERS = ["林岚", "周衡", "陈墨", "赵启", "孙遥", "何瑾"]
STATUSES = ["未开始", "进行中", "已暂停", "已完成", "已取消"]


def build_columns() -> list[dict]:
    columns = []
    for parent, leaves in GROUPS:
        for leaf in leaves:
            columns.append(
                {
                    "col_index": len(columns),
                    "parent": parent,
                    "leaf": leaf,
                    "full_path": f"{parent} / {leaf}",
                }
            )
    return columns


COLUMNS = build_columns()


def mulberry32(seed: int):
    a = seed & 0xFFFFFFFF

    def next_value() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = a
        # 全程按 ToUint32 解释，等价于 JS 中每次位运算后的 >>> 0。
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return next_value


def cell_seed(version_int: int, row_index: int, col_index: int) -> int:
    return (version_int * 1_000_000 + row_index * 1_000 + col_index * 7 + 13) & 0xFFFFFFFF


def cell_text(version: str, row_index: int, col: dict) -> str:
    version_int = 2 if version == "v2" else 1
    rnd = mulberry32(cell_seed(version_int, row_index, col["col_index"]))
    n = int(rnd() * 1_000_000)
    if col["leaf"] == "名称":
        return f"{col['parent'][:2]}-项目-{n}"
    if col["leaf"] == "负责人":
        return OWNERS[n % len(OWNERS)]
    return STATUSES[n % len(STATUSES)]


def expected_matrix(version: str) -> list[list[str]]:
    return [
        [cell_text(version, row_index, col) for col in COLUMNS]
        for row_index in range(ROW_COUNT)
    ]


def expected_cell(version: str, record_id: int, col_index: int) -> str:
    return cell_text(version, record_id - 1, COLUMNS[col_index])
