"""命令行入口：collect / resume / verify / viewer / init-config / serve。"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
from datetime import datetime
from pathlib import Path

from .collector import (
    CollectionError,
    TableCollector,
    UnstableError,
    VersionChangedError,
)
from .config import CollectorConfig, write_sample_config
from .storage import IntegrityError, RunStorage
from .viewer import build_viewer


def _new_run_dir(root: Path) -> Path:
    name = "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    target = root / name
    suffix = 1
    while target.exists():
        suffix += 1
        target = root / f"{name}_{suffix}"
    target.mkdir(parents=True)
    return target


def cmd_init_config(args) -> int:
    path = write_sample_config(args.path)
    print(f"已写入示例配置：{path}")
    return 0


def cmd_serve(args) -> int:
    root = Path(args.root).resolve()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"静态站点服务：http://127.0.0.1:{args.port}/  根目录={root}")
        print("按 Ctrl+C 停止。")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")
    return 0


def _run_collection(cfg: CollectorConfig, run_dir: Path, resume: bool) -> int:
    storage = RunStorage(run_dir)
    try:
        if resume:
            report = storage.recover_running_batch()
            print(f"中断恢复：{report}")
        collector = TableCollector(cfg, storage)
        try:
            result = collector.run()
        except VersionChangedError as exc:
            storage.log(None, "ERROR", str(exc))
            print(f"[失败] {exc}", file=sys.stderr)
            return 3
        except UnstableError as exc:
            if collector.batch_id is not None:
                storage.mark_batch(collector.batch_id, "failed", str(exc))
            storage.log(None, "ERROR", str(exc))
            print(f"[失败] 持续不稳定：{exc}", file=sys.stderr)
            return 4
        except CollectionError as exc:
            if collector.batch_id is not None:
                storage.mark_batch(collector.batch_id, "failed", str(exc))
            storage.log(None, "ERROR", str(exc))
            print(f"[失败] {exc}", file=sys.stderr)
            return 5

        verify = storage.verify_batch(result["batch_id"])
        if not verify["ok"]:
            storage.mark_batch(result["batch_id"], "failed", "完成后校验失败")
            print(f"[失败] 完整性校验未通过：{verify['problems']}", file=sys.stderr)
            return 6
        viewer_path = build_viewer(run_dir, result["batch_id"])
        print(json.dumps({"run_dir": str(run_dir), **result}, ensure_ascii=False, indent=2))
        print(f"离线查看页：{viewer_path}")
        return 0
    finally:
        storage.close()


def cmd_collect(args) -> int:
    cfg = CollectorConfig.load(args.config)
    if args.url:
        cfg.url = args.url
        cfg.validate()
    run_dir = _new_run_dir(Path(cfg.output_dir))
    print(f"运行目录：{run_dir}")
    return _run_collection(cfg, run_dir, resume=False)


def cmd_resume(args) -> int:
    run_dir = Path(args.run_dir)
    if not (run_dir / "collector.db").exists():
        print(f"[失败] 未找到 {run_dir / 'collector.db'}", file=sys.stderr)
        return 2
    cfg = CollectorConfig.load(args.config)
    return _run_collection(cfg, run_dir, resume=True)


def cmd_verify(args) -> int:
    storage = RunStorage(args.run_dir)
    try:
        batch = storage.get_latest_batch() if args.batch_id is None else None
        bid = args.batch_id or int(batch["id"])
        result = storage.verify_batch(bid)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 7
    except IntegrityError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 7
    finally:
        storage.close()


def cmd_viewer(args) -> int:
    path = build_viewer(args.run_dir, args.batch_id)
    print(f"离线查看页已生成：{path}")
    print("直接双击该 HTML，或在浏览器地址栏输入其 file:// 路径即可离线打开。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collector", description="动态虚拟表格采集器（Playwright/Chromium + SQLite）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-config", help="生成示例配置文件")
    p_init.add_argument("path", help="配置文件输出路径，如 config.json")
    p_init.set_defaults(func=cmd_init_config)

    p_serve = sub.add_parser("serve", help="启动本地静态站点服务")
    p_serve.add_argument("--root", default=".", help="静态文件根目录")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(func=cmd_serve)

    p_collect = sub.add_parser("collect", help="新建一次采集")
    p_collect.add_argument("config", help="采集配置 JSON 路径")
    p_collect.add_argument("--url", help="临时覆盖配置中的 URL")
    p_collect.set_defaults(func=cmd_collect)

    p_resume = sub.add_parser("resume", help="复核并续采中断的运行目录")
    p_resume.add_argument("run_dir", help="中断的运行目录")
    p_resume.add_argument("config", help="采集配置 JSON 路径")
    p_resume.set_defaults(func=cmd_resume)

    p_verify = sub.add_parser("verify", help="校验截图哈希、像素区域与单元格完整性")
    p_verify.add_argument("run_dir")
    p_verify.add_argument("--batch-id", type=int, default=None)
    p_verify.set_defaults(func=cmd_verify)

    p_viewer = sub.add_parser("viewer", help="（重新）生成离线证据查看页")
    p_viewer.add_argument("run_dir")
    p_viewer.add_argument("--batch-id", type=int, default=None)
    p_viewer.set_defaults(func=cmd_viewer)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
