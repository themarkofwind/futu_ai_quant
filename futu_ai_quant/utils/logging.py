from __future__ import annotations

import time


def log(stage: str, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")
