"""按 ISO 周切分服务日志，便于按周复盘策略。

文件命名：``data/logs/{service}_{YYYY}-W{WW}.log``
同时维护 ``{service}.log`` 符号链接指向当前周。

用法::

    # 作为管道 tee（读 stdin）
    some_cmd 2>&1 | python -m futu_ai_quant.utils.weeklog tee watchlist

    # 托管子进程并按周写日志（services.sh 使用）
    python -m futu_ai_quant.utils.weeklog run watchlist -- python -u -m futu_ai_quant.cli.watchlist

    # 拆分已有整文件日志
    python -m futu_ai_quant.utils.weeklog split data/logs/watchlist.log data/logs/intraday.log
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import IO

_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\]")
_DEFAULT_LOG_DIR = Path(os.getenv("FUTU_LOG_DIR", "data/logs"))


def iso_week_key(when: date | datetime | None = None) -> str:
    """返回 ISO 周年键，如 ``2026-W32``（周一至周日）。"""
    d = when.date() if isinstance(when, datetime) else (when or date.today())
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def week_log_path(service: str, when: date | datetime | None = None, *, log_dir: Path | None = None) -> Path:
    root = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
    return root / f"{service}_{iso_week_key(when)}.log"


def legacy_log_path(service: str, *, log_dir: Path | None = None) -> Path:
    root = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
    return root / f"{service}.log"


def update_current_symlink(service: str, target: Path, *, log_dir: Path | None = None) -> None:
    """让 ``{service}.log`` 指向当前周日志（相对链接，便于移动目录）。"""
    root = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
    root.mkdir(parents=True, exist_ok=True)
    link = legacy_log_path(service, log_dir=root)
    rel = os.path.relpath(target, start=root)
    if link.is_symlink() or link.exists():
        try:
            if link.is_symlink() and os.readlink(link) == rel:
                return
            link.unlink()
        except OSError:
            pass
    try:
        link.symlink_to(rel)
    except OSError:
        # 某些环境不支持 symlink：退化为同内容提示文件名
        pass


class WeekLogWriter:
    """按 ISO 周自动切换输出文件。"""

    def __init__(self, service: str, *, log_dir: Path | None = None) -> None:
        self.service = service
        self.log_dir = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._week: str | None = None
        self._fp: IO[str] | None = None
        self._path: Path | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def _ensure(self, when: datetime | None = None) -> IO[str]:
        key = iso_week_key(when)
        if self._fp is not None and self._week == key:
            return self._fp
        if self._fp is not None:
            self._fp.flush()
            self._fp.close()
        self._week = key
        self._path = week_log_path(self.service, when, log_dir=self.log_dir)
        self._fp = self._path.open("a", encoding="utf-8", buffering=1)
        update_current_symlink(self.service, self._path, log_dir=self.log_dir)
        return self._fp

    def write_line(self, line: str, *, when: datetime | None = None) -> None:
        fp = self._ensure(when)
        fp.write(line if line.endswith("\n") else line + "\n")

    def write_raw(self, data: str, *, when: datetime | None = None) -> None:
        fp = self._ensure(when)
        fp.write(data)
        fp.flush()

    def close(self) -> None:
        if self._fp is not None:
            self._fp.flush()
            self._fp.close()
            self._fp = None


def parse_line_timestamp(line: str) -> datetime | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def tee_stdin(service: str, *, log_dir: Path | None = None, also_stdout: bool = False) -> int:
    writer = WeekLogWriter(service, log_dir=log_dir)
    try:
        while True:
            chunk = sys.stdin.readline()
            if chunk == "":
                break
            ts = parse_line_timestamp(chunk)
            writer.write_raw(chunk, when=ts)
            if also_stdout:
                sys.stdout.write(chunk)
                sys.stdout.flush()
            # 无时间戳行：周期性检查是否跨周（心跳）
            if ts is None:
                writer._ensure(datetime.now())
    finally:
        writer.close()
    return 0


def run_command(service: str, command: list[str], *, log_dir: Path | None = None) -> int:
    writer = WeekLogWriter(service, log_dir=log_dir)
    # 启动时先建好本周文件与 symlink
    writer._ensure(datetime.now())
    assert writer.path is not None
    print(f"[weeklog] {service} → {writer.path}", flush=True)

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _forward_signal(signum: int, _frame) -> None:
        if proc.poll() is None:
            proc.send_signal(signum)

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    assert proc.stdout is not None
    try:
        while True:
            line = proc.stdout.readline()
            if line == "":
                break
            ts = parse_line_timestamp(line) or datetime.now()
            writer.write_raw(line, when=ts)
            # 跨周时即使没有新行也需切换：在每次写入时已按 ts 切换
    finally:
        writer.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    return int(proc.returncode or 0)


def infer_service_name(path: Path) -> str:
    """从日志文件名推断服务名：watchlist.log / watchlist.log.pre_weekly.bak → watchlist。"""
    name = path.name
    if name.endswith(".pre_weekly.bak"):
        name = name[: -len(".pre_weekly.bak")]
    if name.endswith(".bak"):
        name = name[: -len(".bak")]
    if name.endswith(".log"):
        name = name[: -len(".log")]
    name = re.sub(r"_\d{4}-W\d{2}$", "", name)
    return name or path.stem


def split_log_file(path: Path, *, service: str | None = None, log_dir: Path | None = None) -> dict[str, int]:
    """按行首时间戳把整文件拆到各周；返回 {week_key: lines}。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    name = service or infer_service_name(path)
    root = Path(log_dir) if log_dir is not None else path.parent
    counts: dict[str, int] = {}
    writers: dict[str, IO[str]] = {}
    last_ts: datetime | None = None

    try:
        with path.open("r", encoding="utf-8", errors="replace") as src:
            for line in src:
                ts = parse_line_timestamp(line) or last_ts
                if ts is None:
                    # 文件开头无时间戳：归到文件 mtime 所在周
                    ts = datetime.fromtimestamp(path.stat().st_mtime)
                last_ts = ts
                key = iso_week_key(ts)
                if key not in writers:
                    out = week_log_path(name, ts, log_dir=root)
                    writers[key] = out.open("a", encoding="utf-8")
                    counts[key] = 0
                writers[key].write(line if line.endswith("\n") else line + "\n")
                counts[key] += 1
    finally:
        for fp in writers.values():
            fp.close()

    # 当前周 symlink
    update_current_symlink(name, week_log_path(name, log_dir=root), log_dir=root)
    return counts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按 ISO 周切分服务日志")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="日志目录（默认 data/logs 或环境变量 FUTU_LOG_DIR）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tee = sub.add_parser("tee", help="从 stdin 写入按周日志")
    p_tee.add_argument("service", help="服务名，如 watchlist / intraday / analyze")
    p_tee.add_argument("--also-stdout", action="store_true", help="同时回显到 stdout")

    p_run = sub.add_parser("run", help="运行命令并把输出写入按周日志")
    p_run.add_argument("service", help="服务名")
    p_run.add_argument("command", nargs=argparse.REMAINDER, help="-- 后为命令行")

    p_split = sub.add_parser("split", help="拆分已有整文件日志")
    p_split.add_argument("files", nargs="+", type=Path, help="如 data/logs/watchlist.log")
    p_split.add_argument(
        "--retire",
        action="store_true",
        help="拆分成功后将原文件改名为 *.pre_weekly.bak",
    )

    p_path = sub.add_parser("path", help="打印当前周日志路径")
    p_path.add_argument("service", help="服务名")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    log_dir = args.log_dir

    if args.cmd == "tee":
        return tee_stdin(args.service, log_dir=log_dir, also_stdout=args.also_stdout)

    if args.cmd == "run":
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("run 需要 -- 后的命令")
        return run_command(args.service, command, log_dir=log_dir)

    if args.cmd == "split":
        for f in args.files:
            src = Path(f)
            if args.retire and src.exists() and not src.is_symlink():
                bak = src.with_name(src.name + ".pre_weekly.bak")
                if bak.exists():
                    bak.unlink()
                src.rename(bak)
                src = bak
                print(f"[split] 原文件已改名 → {bak}")
            counts = split_log_file(src, log_dir=log_dir)
            total = sum(counts.values())
            detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"[split] {src} → {total} 行 ({detail})")
        return 0

    if args.cmd == "path":
        p = week_log_path(args.service, log_dir=log_dir)
        print(p)
        return 0

    parser.error(f"未知命令: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
