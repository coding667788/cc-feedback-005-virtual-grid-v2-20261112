"""采集器配置。

采集器对外只接受 URL 与配置文件；不读取任何测试答案或前端私有对象。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CollectorConfig:
    url: str
    output_dir: str = "runs"
    row_step_fraction: float = 0.55
    col_step_fraction: float = 0.7
    settle_rounds: int = 2
    max_window_retries: int = 25
    max_stall_windows: int = 40
    scroll_wait_ms: int = 120
    headless: bool = True
    version_policy: str = "fail"  # fail | restart
    viewport_width: int = 1280
    viewport_height: int = 800

    @staticmethod
    def load(path: str | Path) -> "CollectorConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = CollectorConfig.__dataclass_fields__.keys()
        unknown = set(data) - set(allowed)
        if unknown:
            raise ValueError(f"配置包含未知字段：{sorted(unknown)}")
        cfg = CollectorConfig(**data)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.url.startswith(("http://", "https://", "file://")):
            raise ValueError("url 必须以 http(s):// 或 file:// 开头")
        if self.version_policy not in {"fail", "restart"}:
            raise ValueError("version_policy 仅支持 fail 或 restart")
        if not 0 < self.row_step_fraction <= 1:
            raise ValueError("row_step_fraction 必须位于 (0, 1]")
        if not 0 < self.col_step_fraction <= 1:
            raise ValueError("col_step_fraction 必须位于 (0, 1]")
        if self.max_window_retries < 1 or self.max_stall_windows < 1:
            raise ValueError("重试上限必须为正整数")

    def to_dict(self) -> dict:
        return asdict(self)


def write_sample_config(path: str | Path) -> Path:
    target = Path(path)
    target.write_text(
        json.dumps(
            {
                "url": "http://127.0.0.1:8765/site/index.html",
                "output_dir": "runs",
                "headless": True,
                "version_policy": "fail",
                "max_window_retries": 25,
                "max_stall_windows": 40,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target
