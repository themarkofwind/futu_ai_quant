from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from futu_ai_quant.sim.fees import FeeBreakdown
from futu_ai_quant.sim.jsonl import append_jsonl
from futu_ai_quant.sim.options import resolve_underlying_code
from futu_ai_quant.sim.settings import (
    PORTFOLIO_FILE,
    SIM_INITIAL_CASH,
    SIM_OPTION_CONTRACT_SIZE,
    TRADES_FILE,
)
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float


class PaperPortfolio:
    def __init__(self, path: Path = PORTFOLIO_FILE) -> None:
        self.path = path
        self.data = self._default_data()

    @staticmethod
    def _default_data() -> dict[str, Any]:
        return {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "init_mode": None,
            "cash_hkd": SIM_INITIAL_CASH,
            "stocks": {},
            "options": {},
            "pending_orders": [],
            "stats": {
                "total_trades": 0,
                "total_fees": 0.0,
                "realized_pnl": 0.0,
            },
            "last_decision_id": None,
        }

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> None:
        if not self.path.exists():
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.data["updated_at"] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def init_from_mirror(
        self,
        stocks: list[dict[str, Any]],
        options: list[dict[str, Any]],
        cash_hkd: float,
        option_quote_map: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.data = self._default_data()
        self.data["init_mode"] = "mirror"
        self.data["cash_hkd"] = round(cash_hkd, 2)
        option_quote_map = option_quote_map or {}
        for stock in stocks:
            code = stock["code"]
            self.data["stocks"][code] = {
                "code": code,
                "name": stock.get("name", ""),
                "qty": int(stock.get("qty") or 0),
                "cost_price": safe_float(stock.get("cost_price")) or 0.0,
                "lot_size": int(stock.get("lot_size") or 100),
            }
        for opt in options:
            code = opt["code"]
            qty = int(opt.get("qty") or 0)
            meta = option_quote_map.get(code) or {}
            contract_size = int(
                safe_float(meta.get("contract_size"))
                or SIM_OPTION_CONTRACT_SIZE
            )
            self.data["options"][code] = {
                "code": code,
                "name": opt.get("name", ""),
                "qty": qty,
                "cost_price": safe_float(opt.get("cost_price")) or 0.0,
                "contract_size": contract_size,
                "position_side": "SHORT" if qty < 0 else "LONG",
                "strike_price": meta.get("strike_price"),
                "expire_time": meta.get("expire_time"),
                "option_type": meta.get("option_type"),
                "stock_owner": resolve_underlying_code(
                    code,
                    meta,
                    {"recommendations": []},
                ) or meta.get("stock_owner"),
            }
        self.save()
        log(
            "初始化",
            f"已镜像真实持仓：正股 {len(stocks)}，期权 {len(options)}，现金 {cash_hkd:.2f}",
        )

    def init_from_cash(self, cash_hkd: float) -> None:
        self.data = self._default_data()
        self.data["init_mode"] = "cash"
        self.data["cash_hkd"] = round(cash_hkd, 2)
        self.save()
        log("初始化", f"已创建空白模拟账户，初始现金 {cash_hkd:.2f}")

    def get_stock(self, code: str) -> dict[str, Any] | None:
        return self.data["stocks"].get(code)

    def get_option(self, code: str) -> dict[str, Any] | None:
        return self.data["options"].get(code)

    def upsert_stock(
        self,
        code: str,
        *,
        name: str = "",
        qty_delta: int = 0,
        trade_price: float,
        lot_size: int = 100,
    ) -> dict[str, Any]:
        pos = self.data["stocks"].get(code) or {
            "code": code,
            "name": name,
            "qty": 0,
            "cost_price": 0.0,
            "lot_size": lot_size,
        }
        old_qty = int(pos["qty"])
        new_qty = old_qty + qty_delta
        if new_qty < 0:
            raise ValueError(f"{code} 卖出数量超过持仓")
        if old_qty == 0:
            pos["cost_price"] = trade_price
        elif qty_delta > 0:
            pos["cost_price"] = round(
                (pos["cost_price"] * old_qty + trade_price * qty_delta) / new_qty,
                6,
            )
        pos["qty"] = new_qty
        pos["name"] = name or pos.get("name", "")
        pos["lot_size"] = lot_size or pos.get("lot_size", 100)
        if new_qty == 0:
            self.data["stocks"].pop(code, None)
        else:
            self.data["stocks"][code] = pos
        return pos

    def upsert_option(
        self,
        code: str,
        *,
        name: str = "",
        qty_delta: int = 0,
        trade_price: float,
        contract_size: int = SIM_OPTION_CONTRACT_SIZE,
        position_side: str = "SHORT",
    ) -> dict[str, Any]:
        pos = self.data["options"].get(code) or {
            "code": code,
            "name": name,
            "qty": 0,
            "cost_price": 0.0,
            "contract_size": contract_size,
            "position_side": position_side,
        }
        old_qty = int(pos["qty"])
        new_qty = old_qty + qty_delta
        if old_qty == 0:
            pos["cost_price"] = trade_price
            pos["position_side"] = position_side
        elif (old_qty > 0 and qty_delta > 0) or (old_qty < 0 and qty_delta < 0):
            total_abs = abs(old_qty) + abs(qty_delta)
            pos["cost_price"] = round(
                (pos["cost_price"] * abs(old_qty) + trade_price * abs(qty_delta)) / total_abs,
                6,
            )
        pos["qty"] = new_qty
        pos["name"] = name or pos.get("name", "")
        pos["contract_size"] = contract_size
        if new_qty == 0:
            self.data["options"].pop(code, None)
        else:
            self.data["options"][code] = pos
        return pos

    def record_trade(self, trade: dict[str, Any], fees: FeeBreakdown) -> None:
        self.data["stats"]["total_trades"] += 1
        self.data["stats"]["total_fees"] = round(
            self.data["stats"]["total_fees"] + fees.total,
            4,
        )
        realized = safe_float(trade.get("realized_pnl")) or 0.0
        self.data["stats"]["realized_pnl"] = round(
            self.data["stats"]["realized_pnl"] + realized,
            4,
        )
        append_jsonl(TRADES_FILE, trade)

    def replace_pending_for_code(self, code: str, orders: list[dict[str, Any]]) -> None:
        self.data["pending_orders"] = [
            item for item in self.data["pending_orders"] if item.get("code") != code
        ]
        self.data["pending_orders"].extend(orders)

    def remove_pending(self, order_id: str) -> None:
        self.data["pending_orders"] = [
            item for item in self.data["pending_orders"] if item.get("id") != order_id
        ]
