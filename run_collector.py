"""Windows 原生 Python 顶层入口：python run_collector.py <子命令> ..."""

from collector.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
