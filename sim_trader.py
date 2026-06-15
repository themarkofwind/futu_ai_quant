#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 main.py 交易建议的本地模拟交易脚本。

默认使用报价接口在本地撮合，并计入港股交易成本（佣金、平台费、印花税）。
状态持久化到 data/sim/，用于长期观察策略效果。

用法：
  python sim_trader.py --init-mirror          # 以真实持仓初始化模拟账户
  python sim_trader.py --source latest --once # 按最新决策跑一轮
  python sim_trader.py --source main --once   # 先跑 main 分析再模拟
  python sim_trader.py --report               # 查看累计绩效
  python sim_trader.py --backend futu ...     # 同步提交 Futu 模拟盘订单
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from futu import (
    Currency,
    IndexOptionType,
    OpenQuoteContext,
    OptionType,
    OrderType,
    RET_OK,
    TrdEnv,
    TrdMarket,
    TrdSide,
)

from main import (
    DECISIONS_DIR,
    OPTION_MAX_DAYS,
    OPTION_MIN_DAYS,
    OpenHKTradeContext,
    _build_option_quote_leg,
    classify_positions,
    fetch_snapshot_map,
    get_position_list,
    is_option_code,
    maybe_unlock_trade,
    resolve_analysis_interval,
    run_analysis_cycle,
)

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


def log(stage: str, message: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{stage}] {message}")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_underlying_code(
    option_code: str,
    held_meta: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    owner = str(held_meta.get("stock_owner") or "").strip()
    if owner:
        return owner
    for rec in decision.get("recommendations", []):
        stock_code = str(rec.get("code", ""))
        if is_option_code(stock_code):
            continue
        plan = rec.get("option_trade_plan") or {}
        if str(plan.get("contract_code", "")) == option_code:
            return stock_code
    symbol = option_code.split(".", 1)[-1]
    match = re.match(r"^([A-Z]+)", symbol)
    if match:
        return OPTION_PREFIX_TO_STOCK.get(match.group(1), "")
    return ""


def resolve_contract_size(
    code: str,
    option_quote_map: dict[str, dict[str, Any]],
    portfolio: PaperPortfolio | None = None,
    fallback: int = SIM_OPTION_CONTRACT_SIZE,
) -> int:
    meta = option_quote_map.get(code) or {}
    size = int(safe_float(meta.get("contract_size")) or 0)
    if size > 0:
        return size
    if portfolio:
        pos = portfolio.get_option(code)
        if pos:
            size = int(pos.get("contract_size") or 0)
            if size > 0:
                return size
    return fallback


def fetch_option_quote_map(
    quote_ctx: OpenQuoteContext,
    option_codes: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for code in dict.fromkeys(option_codes):
        if not is_option_code(code):
            continue
        try:
            ret, quote_df = quote_ctx.get_option_quote([_build_option_quote_leg(code)])
            if ret != RET_OK or quote_df is None or quote_df.empty:
                log("期权", f"{code} 行情失败: {quote_df}")
                continue
            row = quote_df.iloc[0]
            contract_size = int(safe_float(row.get("contract_size")) or SIM_OPTION_CONTRACT_SIZE)
            result[code] = {
                "code": code,
                "price": safe_float(row.get("price")),
                "contract_size": contract_size,
                "strike_price": safe_float(row.get("strike_price")),
                "expire_time": str(row.get("expire_time", "")),
                "option_type": str(row.get("option_type", "")).upper(),
                "stock_owner": str(row.get("stock_owner", "")),
                "days_to_expiry": safe_float(row.get("days_to_expiry")),
            }
            log("期权", f"{code} 乘数={contract_size} 价={result[code]['price']}")
        except Exception as exc:
            log("期权", f"{code} 行情异常: {exc}")
    return result


def find_roll_open_leg(
    quote_ctx: OpenQuoteContext,
    held_code: str,
    held_meta: dict[str, Any],
    decision: dict[str, Any],
    option_quote_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    underlying = resolve_underlying_code(held_code, held_meta, decision)
    strike = safe_float(held_meta.get("strike_price"))
    option_type = str(held_meta.get("option_type") or "CALL").upper()
    held_expire = str(held_meta.get("expire_time") or "")

    for rec in decision.get("recommendations", []):
        rec_code = str(rec.get("code", ""))
        if is_option_code(rec_code):
            continue
        if underlying and rec_code != underlying:
            continue
        plan = rec.get("option_trade_plan") or {}
        new_code = str(plan.get("contract_code") or "")
        if (
            plan.get("action") in ("sell_call", "sell_put", "roll")
            and new_code
            and new_code != held_code
        ):
            meta = option_quote_map.get(new_code)
            if meta and meta.get("price") is not None:
                return {
                    "code": new_code,
                    "price": meta["price"],
                    "contract_size": meta["contract_size"],
                    "expire_time": meta.get("expire_time"),
                    "strike_price": meta.get("strike_price"),
                    "source": f"决策 {rec_code} option_trade_plan",
                }
            premium = safe_float(plan.get("premium_per_share"))
            if premium is not None:
                return {
                    "code": new_code,
                    "price": premium,
                    "contract_size": int(plan.get("contract_size") or SIM_OPTION_CONTRACT_SIZE),
                    "expire_time": plan.get("expire_date"),
                    "strike_price": plan.get("strike_price"),
                    "source": f"决策 {rec_code} option_trade_plan",
                }

    if not underlying or strike is None:
        return None

    try:
        ret, exp_df = quote_ctx.get_option_expiration_date(underlying, IndexOptionType.NORMAL)
        if ret != RET_OK or exp_df is None or exp_df.empty:
            return None

        valid_exps = exp_df[
            (exp_df["option_expiry_date_distance"] >= OPTION_MIN_DAYS)
            & (exp_df["option_expiry_date_distance"] <= OPTION_MAX_DAYS)
        ].sort_values("option_expiry_date_distance")

        for _, exp_row in valid_exps.iterrows():
            expiry = str(exp_row["strike_time"])
            if held_expire and expiry <= held_expire:
                continue
            chain_type = OptionType.CALL if option_type == "CALL" else OptionType.PUT
            ret, chain = quote_ctx.get_option_chain(
                underlying,
                start=expiry,
                end=expiry,
                option_type=chain_type,
            )
            if ret != RET_OK or chain is None or chain.empty:
                continue
            matched = chain[chain["strike_price"] == strike]
            if matched.empty:
                continue
            new_code = str(matched.iloc[0]["code"])
            quoted = fetch_option_quote_map(quote_ctx, [new_code])
            meta = quoted.get(new_code)
            if meta and meta.get("price") is not None:
                option_quote_map[new_code] = meta
                return {
                    "code": new_code,
                    "price": meta["price"],
                    "contract_size": meta["contract_size"],
                    "expire_time": meta.get("expire_time"),
                    "strike_price": meta.get("strike_price"),
                    "source": f"期权链远月 {expiry}",
                }
    except Exception as exc:
        log("ROLL", f"{held_code} 扫描远月失败: {exc}")
    return None


class FutuSimBroker:
    """可选：将本地模拟成交同步提交到 Futu 模拟盘。"""

    def __init__(self, trade_ctx: Any) -> None:
        self.trade_ctx = trade_ctx
        self.acc_id = self._resolve_sim_acc_id()

    def _resolve_sim_acc_id(self) -> int:
        ret, data = self.trade_ctx.get_acc_list()
        if ret != RET_OK or data is None:
            raise RuntimeError(f"获取 Futu 账户列表失败: {data}")
        for record in data:
            if record.get("trd_env") == TrdEnv.SIMULATE and TrdMarket.HK in (
                record.get("trdmarket_auth") or []
            ):
                return int(record["acc_id"])
        for record in data:
            if record.get("trd_env") == TrdEnv.SIMULATE:
                return int(record["acc_id"])
        raise RuntimeError("未找到 Futu 港股模拟账户，请先在 OpenD 开通模拟交易")

    def submit_stock(
        self,
        code: str,
        side: str,
        qty: int,
        price: float,
        remark: str = "",
    ) -> dict[str, Any] | None:
        trd_side = TrdSide.BUY if side == "buy" else TrdSide.SELL
        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=OrderType.NORMAL,
            trd_env=TrdEnv.SIMULATE,
            acc_id=self.acc_id,
            remark=remark[:64] if remark else None,
        )
        if ret != RET_OK:
            log("Futu模拟", f"{code} {side} 下单失败: {data}")
            return None
        order_id = None
        if data is not None and not data.empty:
            order_id = data.iloc[0].get("order_id")
        log("Futu模拟", f"{code} {side.upper()} {qty}股 @ {price} order_id={order_id}")
        return {"order_id": order_id, "code": code, "side": side, "qty": qty, "price": price}

    def submit_option(
        self,
        code: str,
        side: str,
        contracts: int,
        price: float,
        remark: str = "",
    ) -> dict[str, Any] | None:
        trd_side = TrdSide.BUY if side == "buy" else TrdSide.SELL
        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=contracts,
            code=code,
            trd_side=trd_side,
            order_type=OrderType.NORMAL,
            trd_env=TrdEnv.SIMULATE,
            acc_id=self.acc_id,
            remark=remark[:64] if remark else None,
        )
        if ret != RET_OK:
            log("Futu模拟", f"{code} {side} 期权下单失败: {data}")
            return None
        order_id = None
        if data is not None and not data.empty:
            order_id = data.iloc[0].get("order_id")
        log("Futu模拟", f"{code} {side.upper()} {contracts}张 @ {price} order_id={order_id}")
        return {"order_id": order_id, "code": code, "side": side, "contracts": contracts, "price": price}


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


@dataclass
class FeeBreakdown:
    commission: float
    platform_fee: float
    stamp_duty: float

    @property
    def total(self) -> float:
        return round(self.commission + self.platform_fee + self.stamp_duty, 4)


class HKCostModel:
    def calc_stock_fees(self, side: str, gross_amount: float) -> FeeBreakdown:
        commission = max(gross_amount * SIM_COMMISSION_RATE, SIM_MIN_COMMISSION)
        stamp = gross_amount * SIM_STAMP_DUTY_RATE if side == "sell" else 0.0
        return FeeBreakdown(
            commission=round(commission, 4),
            platform_fee=SIM_PLATFORM_FEE,
            stamp_duty=round(stamp, 4),
        )

    def calc_option_fees(self, gross_amount: float) -> FeeBreakdown:
        commission = max(gross_amount * SIM_COMMISSION_RATE, SIM_MIN_COMMISSION)
        return FeeBreakdown(
            commission=round(commission, 4),
            platform_fee=SIM_PLATFORM_FEE,
            stamp_duty=0.0,
        )


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


class LocalSimEngine:
    def __init__(
        self,
        portfolio: PaperPortfolio,
        cost_model: HKCostModel,
        futu_broker: FutuSimBroker | None = None,
    ) -> None:
        self.portfolio = portfolio
        self.cost_model = cost_model
        self.futu_broker = futu_broker

    def _price_in_trigger(
        self,
        price: float | None,
        low: float | None,
        high: float | None,
    ) -> bool:
        if price is None:
            return False
        if low is None and high is None:
            return True
        if low is not None and high is not None:
            return low <= price <= high
        if low is not None:
            return price >= low
        if high is not None:
            return price <= high
        return False

    def should_execute_now(
        self,
        rec_action: str,
        direction: str,
        qty: int,
        trigger_low: float | None,
        trigger_high: float | None,
        price: float | None,
    ) -> tuple[bool, str]:
        if qty <= 0 or direction not in ("buy", "sell"):
            return False, "无有效交易计划"
        if SIM_EXECUTION_MODE == "immediate":
            return True, "immediate 模式立即成交"
        if SIM_EXECUTION_MODE == "trigger":
            if self._price_in_trigger(price, trigger_low, trigger_high):
                return True, "价格进入触发区间"
            return False, "等待触发价"
        # hybrid
        if rec_action in ("BUY", "SELL"):
            return True, f"action={rec_action} 立即成交"
        if self._price_in_trigger(price, trigger_low, trigger_high):
            return True, "HOLD 但价格进入触发区间"
        return False, "等待触发价"

    def execute_stock(
        self,
        *,
        code: str,
        name: str,
        side: str,
        qty: int,
        price: float,
        lot_size: int,
        decision_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        if qty <= 0 or price <= 0:
            return None
        gross = round(price * qty, 4)
        fees = self.cost_model.calc_stock_fees(side, gross)
        realized_pnl = 0.0
        pos = self.portfolio.get_stock(code)

        if side == "buy":
            total_cost = gross + fees.total
            if self.portfolio.data["cash_hkd"] < total_cost:
                log("模拟", f"{code} 买入失败：现金不足（需 {total_cost:.2f}）")
                return None
            self.portfolio.data["cash_hkd"] = round(self.portfolio.data["cash_hkd"] - total_cost, 4)
            self.portfolio.upsert_stock(
                code, name=name, qty_delta=qty, trade_price=price, lot_size=lot_size
            )
        else:
            if not pos or int(pos["qty"]) < qty:
                log("模拟", f"{code} 卖出失败：持仓不足")
                return None
            cost_price = safe_float(pos.get("cost_price")) or 0.0
            realized_pnl = round((price - cost_price) * qty - fees.total, 4)
            self.portfolio.data["cash_hkd"] = round(
                self.portfolio.data["cash_hkd"] + gross - fees.total,
                4,
            )
            self.portfolio.upsert_stock(
                code, name=name, qty_delta=-qty, trade_price=price, lot_size=lot_size
            )

        trade = {
            "trade_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "decision_id": decision_id,
            "asset_type": "stock",
            "code": code,
            "name": name,
            "side": side,
            "qty": qty,
            "price": price,
            "gross_amount": gross,
            "fees": {
                "commission": fees.commission,
                "platform_fee": fees.platform_fee,
                "stamp_duty": fees.stamp_duty,
                "total": fees.total,
            },
            "realized_pnl": realized_pnl,
            "reason": reason,
        }
        self.portfolio.record_trade(trade, fees)
        log(
            "成交",
            f"{code} {side.upper()} {qty}股 @ {price} 费用={fees.total:.2f} "
            f"已实现盈亏={realized_pnl:.2f}",
        )
        if self.futu_broker:
            self.futu_broker.submit_stock(code, side, qty, price, remark=reason)
        return trade

    def execute_option_short(
        self,
        *,
        code: str,
        name: str,
        contracts: int,
        price: float,
        contract_size: int,
        decision_id: str,
        reason: str,
        action: str,
    ) -> dict[str, Any] | None:
        if contracts <= 0 or price <= 0:
            return None
        qty = -contracts
        gross = round(price * contract_size * contracts, 4)
        fees = self.cost_model.calc_option_fees(gross)
        net_premium = gross - fees.total
        self.portfolio.data["cash_hkd"] = round(self.portfolio.data["cash_hkd"] + net_premium, 4)
        self.portfolio.upsert_option(
            code,
            name=name,
            qty_delta=qty,
            trade_price=price,
            contract_size=contract_size,
            position_side="SHORT",
        )
        trade = {
            "trade_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "decision_id": decision_id,
            "asset_type": "option",
            "code": code,
            "name": name,
            "side": action,
            "qty": qty,
            "contracts": contracts,
            "price": price,
            "contract_size": contract_size,
            "gross_amount": gross,
            "fees": {
                "commission": fees.commission,
                "platform_fee": fees.platform_fee,
                "stamp_duty": fees.stamp_duty,
                "total": fees.total,
            },
            "realized_pnl": 0.0,
            "reason": reason,
        }
        self.portfolio.record_trade(trade, fees)
        log("成交", f"{code} {action} {contracts}张 @ {price} 乘数={contract_size} 净权利金={net_premium:.2f}")
        if self.futu_broker:
            self.futu_broker.submit_option(code, "sell", contracts, price, remark=reason)
        return trade

    def execute_option_close(
        self,
        *,
        code: str,
        name: str,
        contracts: int,
        price: float,
        contract_size: int,
        decision_id: str,
        reason: str,
    ) -> dict[str, Any] | None:
        pos = self.portfolio.get_option(code)
        if not pos or int(pos["qty"]) >= 0:
            log("模拟", f"{code} 平仓失败：无空头持仓")
            return None
        held = abs(int(pos["qty"]))
        close_qty = min(contracts, held)
        gross = round(price * contract_size * close_qty, 4)
        fees = self.cost_model.calc_option_fees(gross)
        cost_price = safe_float(pos.get("cost_price")) or 0.0
        realized_pnl = round((cost_price - price) * contract_size * close_qty - fees.total, 4)
        self.portfolio.data["cash_hkd"] = round(self.portfolio.data["cash_hkd"] - gross - fees.total, 4)
        self.portfolio.upsert_option(
            code,
            name=name,
            qty_delta=close_qty,
            trade_price=price,
            contract_size=contract_size,
            position_side="SHORT",
        )
        trade = {
            "trade_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(),
            "decision_id": decision_id,
            "asset_type": "option",
            "code": code,
            "name": name,
            "side": "close",
            "qty": close_qty,
            "contracts": close_qty,
            "price": price,
            "contract_size": contract_size,
            "gross_amount": gross,
            "fees": {
                "commission": fees.commission,
                "platform_fee": fees.platform_fee,
                "stamp_duty": fees.stamp_duty,
                "total": fees.total,
            },
            "realized_pnl": realized_pnl,
            "reason": reason,
        }
        self.portfolio.record_trade(trade, fees)
        log(
            "成交",
            f"{code} 平仓 {close_qty}张 @ {price} 乘数={contract_size} 已实现盈亏={realized_pnl:.2f}",
        )
        if self.futu_broker:
            self.futu_broker.submit_option(code, "buy", close_qty, price, remark=reason)
        return trade

    def execute_option_roll(
        self,
        *,
        held_code: str,
        name: str,
        contracts: int,
        close_price: float,
        close_contract_size: int,
        open_leg: dict[str, Any],
        decision_id: str,
    ) -> dict[str, Any] | None:
        close_trade = self.execute_option_close(
            code=held_code,
            name=name,
            contracts=contracts,
            price=close_price,
            contract_size=close_contract_size,
            decision_id=decision_id,
            reason="ROLL 平旧仓",
        )
        if not close_trade:
            return None

        new_code = str(open_leg["code"])
        new_price = safe_float(open_leg.get("price"))
        new_contract_size = int(open_leg.get("contract_size") or close_contract_size)
        if new_price is None:
            log("ROLL", f"{held_code} 平旧成功，但远月 {new_code} 无报价，未开新仓")
            return {"close": close_trade, "open": None}

        open_trade = self.execute_option_short(
            code=new_code,
            name=name,
            contracts=contracts,
            price=new_price,
            contract_size=new_contract_size,
            decision_id=decision_id,
            reason=f"ROLL 开远月 ({open_leg.get('source')})",
            action="sell_call",
        )
        log(
            "ROLL",
            f"{held_code} -> {new_code} {contracts}张 "
            f"平@{close_price} 开@{new_price} 乘数={new_contract_size}",
        )
        return {"close": close_trade, "open": open_trade}


def load_decision_record(source: str, decision_file: str | None) -> tuple[dict[str, Any], str]:
    if source == "file":
        if not decision_file:
            raise ValueError("source=file 需要 --decision-file")
        path = Path(decision_file)
    elif source == "latest":
        path = DECISIONS_DIR / "latest.json"
    else:
        raise ValueError(f"未知 decision source: {source}")

    if not path.exists():
        raise FileNotFoundError(f"决策文件不存在: {path}")

    record = json.loads(path.read_text(encoding="utf-8"))
    decision_id = path.stem
    return record, decision_id


def fetch_market_data(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    stock_codes = [code for code in codes if not is_option_code(code)]
    option_codes = [code for code in codes if is_option_code(code)]
    prices: dict[str, float] = {}
    if stock_codes:
        snapshot_map = fetch_snapshot_map(quote_ctx, list(dict.fromkeys(stock_codes)))
        for code, row in snapshot_map.items():
            price = safe_float(row.get("last_price"))
            if price is not None:
                prices[code] = price
    option_quote_map = fetch_option_quote_map(quote_ctx, option_codes)
    for code, meta in option_quote_map.items():
        if meta.get("price") is not None:
            prices[code] = meta["price"]
    return prices, option_quote_map


def mark_to_market(
    portfolio: PaperPortfolio,
    prices: dict[str, float],
    option_quote_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stock_mv = 0.0
    stock_cost = 0.0
    stock_unrealized = 0.0
    stock_positions: list[dict[str, Any]] = []

    for code, pos in portfolio.data["stocks"].items():
        qty = int(pos["qty"])
        price = prices.get(code, safe_float(pos.get("cost_price")) or 0.0)
        cost = safe_float(pos.get("cost_price")) or 0.0
        mv = price * qty
        unrealized = (price - cost) * qty
        stock_mv += mv
        stock_cost += cost * qty
        stock_unrealized += unrealized
        stock_positions.append(
            {
                "code": code,
                "qty": qty,
                "price": price,
                "market_value": round(mv, 2),
                "unrealized_pnl": round(unrealized, 2),
            }
        )

    option_mv = 0.0
    option_unrealized = 0.0
    option_positions: list[dict[str, Any]] = []
    for code, pos in portfolio.data["options"].items():
        qty = int(pos["qty"])
        contract_size = resolve_contract_size(code, option_quote_map, portfolio)
        price = prices.get(code, safe_float(pos.get("cost_price")) or 0.0)
        cost = safe_float(pos.get("cost_price")) or 0.0
        signed_mv = price * contract_size * qty
        if qty < 0:
            unrealized = (cost - price) * contract_size * abs(qty)
        else:
            unrealized = (price - cost) * contract_size * abs(qty)
        option_mv += signed_mv
        option_unrealized += unrealized
        option_positions.append(
            {
                "code": code,
                "qty": qty,
                "price": price,
                "market_value": round(signed_mv, 2),
                "unrealized_pnl": round(unrealized, 2),
            }
        )

    cash = float(portfolio.data["cash_hkd"])
    total_nav = cash + stock_mv + option_mv
    return {
        "cash_hkd": round(cash, 2),
        "stock_market_value": round(stock_mv, 2),
        "option_market_value": round(option_mv, 2),
        "total_nav": round(total_nav, 2),
        "stock_unrealized_pnl": round(stock_unrealized, 2),
        "option_unrealized_pnl": round(option_unrealized, 2),
        "total_unrealized_pnl": round(stock_unrealized + option_unrealized, 2),
        "realized_pnl": portfolio.data["stats"]["realized_pnl"],
        "total_fees": portfolio.data["stats"]["total_fees"],
        "stock_positions": stock_positions,
        "option_positions": option_positions,
        "pending_orders": len(portfolio.data["pending_orders"]),
    }


def process_pending_orders(
    engine: LocalSimEngine,
    portfolio: PaperPortfolio,
    prices: dict[str, float],
    decision_id: str,
) -> int:
    executed = 0
    for order in list(portfolio.data["pending_orders"]):
        code = order["code"]
        price = prices.get(code)
        low = safe_float(order.get("trigger_low"))
        high = safe_float(order.get("trigger_high"))
        if not engine._price_in_trigger(price, low, high):
            continue
        side = order["side"]
        qty = int(order["qty"])
        if order.get("asset_type") == "stock":
            trade = engine.execute_stock(
                code=code,
                name=order.get("name", ""),
                side=side,
                qty=qty,
                price=price or 0.0,
                lot_size=int(order.get("lot_size") or 100),
                decision_id=decision_id,
                reason=f"触发挂单 {order.get('id')}",
            )
            if trade:
                portfolio.remove_pending(order["id"])
                executed += 1
    return executed


def apply_recommendations(
    engine: LocalSimEngine,
    portfolio: PaperPortfolio,
    decision: dict[str, Any],
    prices: dict[str, float],
    option_quote_map: dict[str, dict[str, Any]],
    decision_id: str,
    quote_ctx: OpenQuoteContext,
) -> dict[str, int]:
    counters = {"executed": 0, "pending": 0, "skipped": 0}
    for rec in decision.get("recommendations", []):
        code = str(rec.get("code", ""))
        if not code:
            continue

        stock_plan = rec.get("stock_trade_plan") or {}
        option_plan = rec.get("option_trade_plan") or {}
        action = str(rec.get("action", "HOLD")).upper()
        name = str(rec.get("name", ""))

        if not is_option_code(code):
            direction = str(stock_plan.get("direction", "none")).lower()
            qty = int(stock_plan.get("suggested_qty") or 0)
            lot_size = int(stock_plan.get("lot_size") or 100)
            trigger_low = safe_float(stock_plan.get("trigger_price_low"))
            trigger_high = safe_float(stock_plan.get("trigger_price_high"))
            price = prices.get(code)

            should_exec, reason = engine.should_execute_now(
                action, direction, qty, trigger_low, trigger_high, price
            )
            if qty > 0 and direction in ("buy", "sell"):
                if should_exec and price is not None:
                    trade = engine.execute_stock(
                        code=code,
                        name=name,
                        side=direction,
                        qty=qty,
                        price=price,
                        lot_size=lot_size,
                        decision_id=decision_id,
                        reason=reason,
                    )
                    if trade:
                        counters["executed"] += 1
                    else:
                        counters["skipped"] += 1
                else:
                    pending = {
                        "id": str(uuid.uuid4()),
                        "code": code,
                        "name": name,
                        "asset_type": "stock",
                        "side": direction,
                        "qty": qty,
                        "lot_size": lot_size,
                        "trigger_low": trigger_low,
                        "trigger_high": trigger_high,
                        "created_at": datetime.now().isoformat(),
                        "decision_id": decision_id,
                        "note": reason,
                    }
                    portfolio.replace_pending_for_code(code, [pending])
                    counters["pending"] += 1
                    log("挂单", f"{code} {direction} {qty}股 触发区间 {trigger_low}-{trigger_high}")
            else:
                counters["skipped"] += 1

            opt_action = str(option_plan.get("action", "none")).lower()
            contracts = int(option_plan.get("contracts") or 0)
            contract_code = str(option_plan.get("contract_code") or "")
            if opt_action in ("sell_call", "sell_put") and contracts > 0 and contract_code:
                if action == "HOLD":
                    counters["skipped"] += 1
                    continue
                existing = portfolio.get_option(contract_code)
                if existing and int(existing.get("qty") or 0) < 0:
                    counters["skipped"] += 1
                    continue
                if opt_action == "sell_call":
                    stock_pos = portfolio.get_stock(code)
                    held = int(stock_pos["qty"]) if stock_pos else 0
                    contract_size = resolve_contract_size(
                        contract_code, option_quote_map, portfolio
                    )
                    max_cover = held // contract_size if contract_size else 0
                    if max_cover <= 0:
                        counters["skipped"] += 1
                        continue
                    contracts = min(contracts, max_cover)
                premium = prices.get(contract_code)
                if premium is None:
                    premium = safe_float(option_plan.get("premium_per_share"))
                if premium is None:
                    counters["skipped"] += 1
                    continue
                contract_size = resolve_contract_size(
                    contract_code, option_quote_map, portfolio
                )
                trade = engine.execute_option_short(
                    code=contract_code,
                    name=name,
                    contracts=contracts,
                    price=premium,
                    contract_size=contract_size,
                    decision_id=decision_id,
                    reason=f"{code} 配套 {opt_action}",
                    action=opt_action,
                )
                if trade:
                    counters["executed"] += 1
        else:
            opt_action = str(option_plan.get("action", "none")).lower()
            contracts = int(option_plan.get("contracts") or 0)
            held_pos = portfolio.get_option(code) or {}
            if contracts <= 0:
                contracts = abs(int(held_pos.get("qty") or 0)) or 1
            price = prices.get(code) or safe_float(option_plan.get("premium_per_share"))
            close_contract_size = resolve_contract_size(code, option_quote_map, portfolio)
            held_meta = {
                **(option_quote_map.get(code) or {}),
                "strike_price": held_pos.get("strike_price") or option_quote_map.get(code, {}).get("strike_price"),
                "expire_time": held_pos.get("expire_time") or option_quote_map.get(code, {}).get("expire_time"),
                "option_type": held_pos.get("option_type") or option_quote_map.get(code, {}).get("option_type"),
                "stock_owner": held_pos.get("stock_owner") or option_quote_map.get(code, {}).get("stock_owner"),
            }
            if action == "ROLL" or opt_action == "roll":
                if price is None:
                    counters["skipped"] += 1
                    continue
                open_leg = find_roll_open_leg(
                    quote_ctx, code, held_meta, decision, option_quote_map
                )
                if open_leg:
                    roll_result = engine.execute_option_roll(
                        held_code=code,
                        name=name,
                        contracts=contracts,
                        close_price=price,
                        close_contract_size=close_contract_size,
                        open_leg=open_leg,
                        decision_id=decision_id,
                    )
                    if roll_result:
                        counters["executed"] += 1
                    else:
                        counters["skipped"] += 1
                else:
                    trade = engine.execute_option_close(
                        code=code,
                        name=name,
                        contracts=contracts,
                        price=price,
                        contract_size=close_contract_size,
                        decision_id=decision_id,
                        reason="ROLL 仅平仓（未找到远月合约）",
                    )
                    if trade:
                        counters["executed"] += 1
                    else:
                        counters["skipped"] += 1
            elif opt_action == "close" and price is not None:
                trade = engine.execute_option_close(
                    code=code,
                    name=name,
                    contracts=contracts,
                    price=price,
                    contract_size=close_contract_size,
                    decision_id=decision_id,
                    reason="建议平仓",
                )
                if trade:
                    counters["executed"] += 1
                else:
                    counters["skipped"] += 1
            else:
                counters["skipped"] += 1

    return counters


def save_snapshot(
    portfolio: PaperPortfolio,
    mtm: dict[str, Any],
    decision_id: str,
    counters: dict[str, int],
) -> None:
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "decision_id": decision_id,
        "execution": counters,
        **mtm,
    }
    append_jsonl(SNAPSHOTS_FILE, snapshot)
    metrics = {
        "updated_at": snapshot["timestamp"],
        "latest_nav": mtm["total_nav"],
        "cash_hkd": mtm["cash_hkd"],
        "total_unrealized_pnl": mtm["total_unrealized_pnl"],
        "realized_pnl": mtm["realized_pnl"],
        "total_fees": mtm["total_fees"],
        "total_trades": portfolio.data["stats"]["total_trades"],
        "pending_orders": mtm["pending_orders"],
        "last_decision_id": decision_id,
    }
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    METRICS_FILE.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


def print_report() -> None:
    if not METRICS_FILE.exists():
        log("报告", "尚无模拟数据，请先运行 sim_trader.py")
        return
    metrics = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    print("\n===== 模拟交易绩效 =====")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if SNAPSHOTS_FILE.exists():
        lines = SNAPSHOTS_FILE.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            first = json.loads(lines[0])
            last = json.loads(lines[-1])
            nav_change = last["total_nav"] - first["total_nav"]
            print(
                f"\n净值：{first['total_nav']:.2f} -> {last['total_nav']:.2f} "
                f"（{nav_change:+.2f}） 快照数={len(lines)}"
            )
    print()


def init_mirror_portfolio(
    quote_ctx: OpenQuoteContext,
    trade_ctx: Any,
    portfolio: PaperPortfolio,
) -> None:
    ret, positions = get_position_list(trade_ctx)
    if ret != RET_OK:
        raise RuntimeError(f"拉取真实持仓失败: {positions}")
    stocks, options = classify_positions(positions, quote_ctx)
    option_codes = [opt["code"] for opt in options]
    option_quote_map = fetch_option_quote_map(quote_ctx, option_codes)
    cash = SIM_INITIAL_CASH
    try:
        ret, acc = trade_ctx.accinfo_query(trd_env=TrdEnv.REAL, currency=Currency.HKD)
        if ret == RET_OK and acc is not None and not acc.empty:
            cash = safe_float(acc.iloc[0].get("cash")) or SIM_INITIAL_CASH
    except Exception as exc:
        log("初始化", f"读取真实现金失败，使用默认 {SIM_INITIAL_CASH}: {exc}")
    portfolio.init_from_mirror(stocks, options, cash, option_quote_map)


def collect_price_codes(portfolio: PaperPortfolio, decision: dict[str, Any]) -> list[str]:
    codes = set(portfolio.data["stocks"]) | set(portfolio.data["options"])
    for rec in decision.get("recommendations", []):
        code = rec.get("code")
        if code:
            codes.add(str(code))
        option_plan = rec.get("option_trade_plan") or {}
        contract_code = option_plan.get("contract_code")
        if contract_code:
            codes.add(str(contract_code))
    for order in portfolio.data["pending_orders"]:
        if order.get("code"):
            codes.add(str(order["code"]))
    return sorted(codes)


def enrich_option_quote_map_for_decision(
    quote_ctx: OpenQuoteContext,
    decision: dict[str, Any],
    option_quote_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    extra_codes: list[str] = []
    for rec in decision.get("recommendations", []):
        plan = rec.get("option_trade_plan") or {}
        contract_code = plan.get("contract_code")
        if contract_code and contract_code not in option_quote_map:
            extra_codes.append(str(contract_code))
        code = rec.get("code")
        if code and is_option_code(str(code)) and code not in option_quote_map:
            extra_codes.append(str(code))
    if extra_codes:
        option_quote_map.update(fetch_option_quote_map(quote_ctx, extra_codes))
    return option_quote_map


def run_sim_cycle(
    quote_ctx: OpenQuoteContext,
    portfolio: PaperPortfolio,
    engine: LocalSimEngine,
    *,
    source: str,
    decision_file: str | None,
    trade_ctx: Any | None = None,
    ai_client: Any | None = None,
) -> None:
    if source == "main":
        if trade_ctx is None or ai_client is None:
            raise ValueError("source=main 需要交易上下文与 AI 客户端")
        result = run_analysis_cycle(
            quote_ctx,
            trade_ctx,
            ai_client,
            print_decision=False,
            save_decision=True,
        )
        if not result:
            raise RuntimeError("main 分析未产生有效决策")
        record = {
            "saved_at": datetime.now().isoformat(),
            "decision": result["decision"],
            "required_codes": result["required_codes"],
            "payload_summary": result.get("payload_summary"),
        }
        decision_id = Path(result["saved_path"]).stem if result.get("saved_path") else "main_live"
    else:
        record, decision_id = load_decision_record(source, decision_file)

    decision = record["decision"]
    portfolio.data["last_decision_id"] = decision_id
    codes = collect_price_codes(portfolio, decision)
    prices, option_quote_map = fetch_market_data(quote_ctx, codes)
    option_quote_map = enrich_option_quote_map_for_decision(
        quote_ctx, decision, option_quote_map
    )

    triggered = process_pending_orders(engine, portfolio, prices, decision_id)
    if triggered:
        log("模拟", f"触发成交挂单 {triggered} 笔")

    counters = apply_recommendations(
        engine,
        portfolio,
        decision,
        prices,
        option_quote_map,
        decision_id,
        quote_ctx,
    )
    prices, option_quote_map = fetch_market_data(
        quote_ctx, collect_price_codes(portfolio, decision)
    )
    mtm = mark_to_market(portfolio, prices, option_quote_map)
    save_snapshot(portfolio, mtm, decision_id, counters)
    portfolio.save()

    log(
        "汇总",
        f"成交={counters['executed']} 挂单={counters['pending']} 跳过={counters['skipped']} "
        f"净值={mtm['total_nav']:.2f} 现金={mtm['cash_hkd']:.2f} "
        f"浮盈={mtm['total_unrealized_pnl']:.2f} 已实现={mtm['realized_pnl']:.2f}",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于 main.py 建议的港股模拟交易")
    parser.add_argument("--once", action="store_true", help="只运行一轮后退出")
    parser.add_argument(
        "--source",
        choices=["latest", "main", "file"],
        default="latest",
        help="决策来源：latest=最近保存 / main=实时分析 / file=指定文件",
    )
    parser.add_argument("--decision-file", help="source=file 时指定决策 JSON 路径")
    parser.add_argument("--init-mirror", action="store_true", help="用真实持仓初始化模拟账户")
    parser.add_argument("--init-cash", type=float, help="用指定现金初始化空白模拟账户")
    parser.add_argument("--report", action="store_true", help="打印累计绩效后退出")
    parser.add_argument(
        "--backend",
        choices=["local", "futu", "both"],
        default=None,
        help="local=仅本地撮合；futu=同步提交 Futu 模拟盘；both=本地+Futu",
    )
    return parser.parse_args()


def resolve_backend(args: argparse.Namespace) -> str:
    backend = (args.backend or SIM_BACKEND or "local").lower()
    if backend not in ("local", "futu", "both"):
        raise ValueError(f"不支持的 backend: {backend}")
    return backend


def build_engine(
    portfolio: PaperPortfolio,
    trade_ctx: Any | None,
    backend: str,
) -> LocalSimEngine:
    futu_broker = None
    if backend in ("futu", "both"):
        if trade_ctx is None:
            raise RuntimeError("backend=futu/both 需要 Futu 交易上下文")
        futu_broker = FutuSimBroker(trade_ctx)
        log("Futu模拟", f"已连接模拟账户 acc_id={futu_broker.acc_id}")
    return LocalSimEngine(portfolio, HKCostModel(), futu_broker=futu_broker)


def main() -> None:
    load_dotenv()
    args = parse_args()
    backend = resolve_backend(args)

    if args.report:
        print_report()
        return

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    portfolio = PaperPortfolio()
    portfolio.load()

    quote_ctx: OpenQuoteContext | None = None
    trade_ctx: Any | None = None
    ai_client: Any | None = None

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)

        if args.init_mirror or args.init_cash is not None:
            if args.init_mirror:
                trade_ctx = OpenHKTradeContext(filter_trdmarket=TrdMarket.HK, host=host, port=port)
                maybe_unlock_trade(trade_ctx)
                init_mirror_portfolio(quote_ctx, trade_ctx, portfolio)
            else:
                portfolio.init_from_cash(args.init_cash)
            if not args.once:
                log("初始化", "初始化完成。请再运行 sim_trader.py --source latest --once 开始模拟")
                return

        if not portfolio.exists():
            raise RuntimeError(
                "模拟账户未初始化，请先运行 --init-mirror 或 --init-cash 1000000"
            )

        if args.source == "main":
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("source=main 需要在 .env 配置 DEEPSEEK_API_KEY")
            ai_client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            )
            trade_ctx = trade_ctx or OpenHKTradeContext(
                filter_trdmarket=TrdMarket.HK, host=host, port=port
            )
            maybe_unlock_trade(trade_ctx)

        if backend in ("futu", "both"):
            trade_ctx = trade_ctx or OpenHKTradeContext(
                filter_trdmarket=TrdMarket.HK, host=host, port=port
            )
            maybe_unlock_trade(trade_ctx)

        engine = build_engine(portfolio, trade_ctx, backend)
        log("模拟", f"backend={backend} 执行模式={SIM_EXECUTION_MODE}")

        if args.once:
            run_sim_cycle(
                quote_ctx,
                portfolio,
                engine,
                source=args.source,
                decision_file=args.decision_file,
                trade_ctx=trade_ctx,
                ai_client=ai_client,
            )
            return

        while True:
            try:
                run_sim_cycle(
                    quote_ctx,
                    portfolio,
                    engine,
                    source=args.source,
                    decision_file=args.decision_file,
                    trade_ctx=trade_ctx,
                    ai_client=ai_client,
                )
            except Exception as exc:
                log("循环", f"本轮模拟异常: {exc}")
                traceback.print_exc()

            interval_sec, interval_reason = resolve_analysis_interval()
            log("循环", f"{interval_reason}，等待 {interval_sec} 秒...")
            time.sleep(interval_sec)
    finally:
        if quote_ctx is not None:
            quote_ctx.close()
        if trade_ctx is not None:
            trade_ctx.close()
        log("连接", "连接已关闭")


if __name__ == "__main__":
    main()
