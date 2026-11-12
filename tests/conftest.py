"""pytest 公共夹具：静态站点服务、Playwright、独立临时运行目录。"""

from __future__ import annotations

import functools
import http.server
import socket
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def site_server():
    port = _free_port()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(PROJECT_ROOT)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def playwright_ctx():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page_factory(playwright_ctx):
    pages = []

    def _make(url: str):
        page = playwright_ctx.new_page(
            viewport={"width": 1280, "height": 800}, device_scale_factor=1
        )
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("#vgViewport .vg-cell", timeout=15000)
        page.wait_for_timeout(300)
        pages.append(page)
        return page

    yield _make
    for page in pages:
        page.close()


@pytest.fixture()
def make_config(tmp_path):
    def _make(url: str, **overrides):
        cfg = {
            "url": url,
            "output_dir": str(tmp_path / "runs"),
            "headless": True,
            "max_window_retries": overrides.pop("max_window_retries", 25),
            "max_stall_windows": overrides.pop("max_stall_windows", 40),
        }
        cfg.update(overrides)
        path = tmp_path / "config.json"
        path.write_text(
            __import__("json").dumps(cfg, ensure_ascii=False), encoding="utf-8"
        )
        return path, tmp_path

    return _make
