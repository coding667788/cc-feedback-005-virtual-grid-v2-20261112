"""边界测试：采集器只能通过公开 DOM/交互/截图工作。"""

from pathlib import Path


COLLECTOR_DIR = Path(__file__).resolve().parents[1] / "collector"

FORBIDDEN_TOKENS = [
    "__VG_PRIVATE__",
    "answer_key",
    "page.on(",
    "context.on(",
    "route(",
    "expect_response",
    "wait_for_response",
    "response.body",
    "fetch(",
    "XMLHttpRequest",
]


def test_collector_source_has_no_private_or_network_access():
    sources = list(COLLECTOR_DIR.glob("*.py"))
    assert sources
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name} 出现禁止能力：{token}"


def test_collector_only_uses_public_meta_and_dom():
    text = (COLLECTOR_DIR / "collector.py").read_text(encoding="utf-8")
    assert "#vgMeta [data-meta]" in text
    assert ".vg-cell[data-full-path]" in text
    assert ".vg-hleaf[data-full-path]" in text
    assert "screenshot()" in text
    # 不允许读取 window 上的私有挂载点。
    assert "window.__" not in text
