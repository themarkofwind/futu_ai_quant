from __future__ import annotations

from typing import Any

from futu import OrderType, RET_OK, TrdEnv, TrdMarket, TrdSide

from futu_ai_quant.utils.logging import log


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
