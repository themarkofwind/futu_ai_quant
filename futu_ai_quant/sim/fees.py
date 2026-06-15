from __future__ import annotations

from dataclasses import dataclass

from futu_ai_quant.sim.settings import (
    SIM_COMMISSION_RATE,
    SIM_MIN_COMMISSION,
    SIM_PLATFORM_FEE,
    SIM_STAMP_DUTY_RATE,
)


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
