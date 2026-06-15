from __future__ import annotations

import os
from pathlib import Path


SIM_DATA_DIR = Path(os.getenv("SIM_DATA_DIR", "data/sim"))
PORTFOLIO_FILE = SIM_DATA_DIR / "portfolio.json"
TRADES_FILE = SIM_DATA_DIR / "trades.jsonl"
SNAPSHOTS_FILE = SIM_DATA_DIR / "snapshots.jsonl"
METRICS_FILE = SIM_DATA_DIR / "metrics.json"

SIM_INITIAL_CASH = float(os.getenv("SIM_INITIAL_CASH", "1000000"))
SIM_COMMISSION_RATE = float(os.getenv("SIM_COMMISSION_RATE", "0.0003"))
SIM_MIN_COMMISSION = float(os.getenv("SIM_MIN_COMMISSION", "3"))
SIM_PLATFORM_FEE = float(os.getenv("SIM_PLATFORM_FEE", "15"))
SIM_STAMP_DUTY_RATE = float(os.getenv("SIM_STAMP_DUTY_RATE", "0.0013"))
SIM_EXECUTION_MODE = os.getenv("SIM_EXECUTION_MODE", "hybrid").lower()
SIM_BACKEND = os.getenv("SIM_BACKEND", "local").lower()
SIM_OPTION_CONTRACT_SIZE = int(os.getenv("SIM_OPTION_CONTRACT_SIZE", "100"))
OPTION_PREFIX_TO_STOCK = {
    "ALB": "HK.09988",
    "TCH": "HK.00700",
    "KST": "HK.01024",
    "JXC": "HK.00358",
    "ALC": "HK.02600",
}
