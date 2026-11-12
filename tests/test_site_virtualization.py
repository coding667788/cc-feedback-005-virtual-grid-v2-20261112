"""节点复用、横纵虚拟滚动、冻结编号列、两层表头与同名列。"""


def test_dom_node_pool_is_reused_on_vertical_scroll(site_server, page_factory):
    page = page_factory(f"{site_server}/site/index.html")
    before = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('#vgBody .vg-row'))
          .map((el) => el)
        """
    )
    tags_before = page.evaluate(
        "Array.from(document.querySelectorAll('#vgBody .vg-row')).map((el) => el.dataset.rowSlot)"
    )
    assert len(tags_before) < 30  # 仅渲染视口附近的少量行槽

    page.evaluate(
        "() => { document.getElementById('vgViewport').scrollTop = 3000; }"
    )
    page.wait_for_timeout(250)
    tags_after = page.evaluate(
        "Array.from(document.querySelectorAll('#vgBody .vg-row')).map((el) => el.dataset.rowSlot)"
    )
    assert tags_before == tags_after  # 行槽 DOM 节点被复用，没有新建/销毁

    bound_ids = page.evaluate(
        "Array.from(document.querySelectorAll('#vgBody .vg-row'))"
        ".map((el) => el.dataset.recordId).filter(Boolean).map(Number)"
    )
    assert max(bound_ids) > 80  # 复用的节点已经绑定到后面的记录


def test_horizontal_virtualization_and_frozen_id(site_server, page_factory):
    page = page_factory(f"{site_server}/site/index.html")
    cell_count_before = page.evaluate(
        "document.querySelectorAll('#vgBody .vg-cell[data-full-path]').length"
    )
    # 12 列数据，但每行只实例化视口附近的单元格槽。
    row_slots = page.evaluate("document.querySelectorAll('#vgBody .vg-row').length")
    assert cell_count_before < row_slots * 12

    page.evaluate(
        "() => { document.getElementById('vgViewport').scrollLeft = 900; }"
    )
    page.wait_for_timeout(250)
    visible_cols = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('#vgBody .vg-row:first-child .vg-cell[data-full-path]'))
          .map((el) => Number(el.dataset.colIndex))
        """
    )
    assert max(visible_cols) > 4

    frozen = page.query_selector("#vgBody .vg-cell-id")
    assert frozen is not None
    position = frozen.evaluate("(el) => getComputedStyle(el).position")
    assert position == "sticky"


def test_two_level_header_same_named_columns_and_public_meta(site_server, page_factory):
    page = page_factory(f"{site_server}/site/index.html")
    groups = page.evaluate(
        "Array.from(document.querySelectorAll('.vg-hgroup')).map((el) => el.dataset.parent)"
    )
    leaves = page.evaluate(
        "Array.from(document.querySelectorAll('.vg-hleaf')).map((el) => el.dataset.leaf)"
    )
    full_paths = page.evaluate(
        "Array.from(document.querySelectorAll('.vg-hleaf')).map((el) => el.dataset.fullPath)"
    )
    assert groups == ["销售中心", "交付中心", "供应链", "财务中心"]
    assert leaves.count("名称") == 4
    assert leaves.count("状态") == 4
    assert len(set(full_paths)) == 12

    meta = page.evaluate(
        """
        () => Object.fromEntries(Array.from(document.querySelectorAll('#vgMeta [data-meta]'))
          .map((el) => [el.dataset.meta, el.textContent.trim()]))
        """
    )
    assert meta["recordCount"] == "250"
    assert meta["columnCount"] == "12"
    assert meta["version"] == "v1"
    assert meta["loading"] == "ready"
    seq_before = int(meta["viewSeq"])
    page.click("#vgRedrawBtn")
    page.wait_for_timeout(150)
    seq_after = int(
        page.text_content("#vgMeta [data-meta=viewSeq]")
    )
    assert seq_after == seq_before + 1
