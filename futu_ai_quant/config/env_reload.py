"""监听 .env 变更并热加载到进程环境（无需重启）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from futu_ai_quant.strategy import intraday_t_settings as its


def resolve_env_path(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    found = find_dotenv(usecwd=True)
    if found:
        return Path(found)
    cwd_env = Path.cwd() / ".env"
    return cwd_env if cwd_env.is_file() else None


@dataclass
class EnvReloader:
    """按文件 mtime 检测 .env 变化，覆盖加载并刷新日内做 T 配置。"""

    env_path: Path | None = None
    check_interval_sec: float = 2.0
    _mtime: float | None = field(default=None, init=False, repr=False)
    _last_check_at: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.env_path is None:
            self.env_path = resolve_env_path()
        if self.env_path is not None and self.env_path.is_file():
            self._mtime = self.env_path.stat().st_mtime

    def poll(self, *, now: float | None = None, force: bool = False) -> dict[str, tuple[object, object]]:
        """
        若 .env 有更新则 ``load_dotenv(override=True)`` 并刷新 settings。

        返回变更的 settings 键 -> (旧值, 新值)；无变更返回空 dict。
        """
        import time

        ts = time.time() if now is None else now
        if not force and (ts - self._last_check_at) < self.check_interval_sec:
            return {}
        self._last_check_at = ts

        path = self.env_path or resolve_env_path()
        if path is None or not path.is_file():
            return {}

        mtime = path.stat().st_mtime
        if not force and self._mtime is not None and mtime <= self._mtime:
            return {}

        self.env_path = path
        self._mtime = mtime
        load_dotenv(path, override=True)
        # 允许运行中临时用环境变量关掉 Bark
        if os.getenv("BARK_ENABLED", "").strip() == "":
            pass
        return its.refresh_from_environ()


def format_settings_changes(changes: dict[str, tuple[object, object]]) -> str:
    if not changes:
        return ""
    parts = [f"{k}: {old!r} → {new!r}" for k, (old, new) in sorted(changes.items())]
    return "; ".join(parts)
