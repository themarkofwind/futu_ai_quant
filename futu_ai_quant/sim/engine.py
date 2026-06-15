from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from futu_ai_quant.domain.positions import infer_option_right
from futu_ai_quant.market.triggers import price_in_trigger
from futu_ai_quant.sim.broker import FutuSimBroker
from futu_ai_quant.sim.fees import HKCostModel
from futu_ai_quant.sim.portfolio import PaperPortfolio
from futu_ai_quant.sim.settings import SIM_EXECUTION_MODE
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float


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

    @staticmethod
    def _roll_open_action(open_leg: dict[str, Any], held_code: str) -> str:
        opt_type = str(open_leg.get("option_type") or "").upper()
        if not opt_type:
            inferred = infer_option_right(str(open_leg.get("code") or held_code))
            opt_type = (inferred or "CALL").upper()
        return "sell_put" if opt_type == "PUT" else "sell_call"

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
            if price_in_trigger(price, trigger_low, trigger_high):
                return True, "价格进入触发区间"
            return False, "等待触发价"
        # hybrid
        if rec_action in ("BUY", "SELL"):
            return True, f"action={rec_action} 立即成交"
        if price_in_trigger(price, trigger_low, trigger_high):
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
            action=self._roll_open_action(open_leg, held_code),
        )
        log(
            "ROLL",
            f"{held_code} -> {new_code} {contracts}张 "
            f"平@{close_price} 开@{new_price} 乘数={new_contract_size}",
        )
        return {"close": close_trade, "open": open_trade}
