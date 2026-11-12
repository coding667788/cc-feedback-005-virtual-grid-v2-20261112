"""已知答案与站点公开 DOM 逐格一致（测试可读答案，通过浏览器核对）。"""

from tests.answer_key import COLUMNS, ROW_COUNT, expected_cell


def test_answer_key_matches_public_dom(site_server, page_factory):
    page = page_factory(f"{site_server}/site/index.html")
    page.wait_for_function(
        "document.querySelector('#vgMeta [data-meta=recordCount]').textContent === '250'"
    )

    # 纵向遍历全部行，逐格比对公开 DOM 文本与已知答案。
    page.evaluate(
        """
        () => { document.getElementById('vgViewport').scrollTop = 0; }
        """
    )
    page.wait_for_timeout(300)
    max_top = page.evaluate(
        "document.getElementById('vgViewport').scrollHeight "
        "- document.getElementById('vgViewport').clientHeight"
    )
    checked = 0
    top = 0
    while top <= max_top:
        page.evaluate(
            "(t) => { document.getElementById('vgViewport').scrollTop = t; }", top
        )
        page.wait_for_timeout(120)
        cells = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('#vgViewport .vg-cell[data-full-path]'))
              .map((el) => ({
                recordId: Number(el.dataset.recordId),
                colIndex: Number(el.dataset.colIndex),
                text: el.textContent
              }))
            """
        )
        for cell in cells:
            expected = expected_cell("v1", cell["recordId"], cell["colIndex"])
            assert cell["text"] == expected
            checked += 1
        top += 200

    assert checked >= ROW_COUNT * len(COLUMNS)


def test_v2_answer_key_differs_and_matches(site_server, page_factory):
    page = page_factory(f"{site_server}/site/index.html?version=v2")
    page.wait_for_function(
        "document.querySelector('#vgMeta [data-meta=version]').textContent === 'v2'"
    )
    page.evaluate("() => { document.getElementById('vgViewport').scrollTop = 600; }")
    page.wait_for_timeout(200)
    cells = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('#vgViewport .vg-cell[data-full-path]'))
          .slice(0, 12).map((el) => ({
            recordId: Number(el.dataset.recordId),
            colIndex: Number(el.dataset.colIndex),
            text: el.textContent
          }))
        """
    )
    diffs = 0
    for cell in cells:
        assert cell["text"] == expected_cell("v2", cell["recordId"], cell["colIndex"])
        if cell["text"] != expected_cell("v1", cell["recordId"], cell["colIndex"]):
            diffs += 1
    assert diffs > 0
