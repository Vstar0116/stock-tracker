"""Pure BS-V4 Zone Classifier logic: no I/O, no database access.

Zone codes and labels are neutral technical-state descriptions, not
buy/sell advice -- see docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md
for why. The math/thresholds match the original request exactly; only the
naming changed.
"""

from __future__ import annotations

from dataclasses import dataclass

ZONE_LABELS = {
    "A": "Pullback at Support",
    "B": "Mid-RSI Above Trend",
    "C": "Elevated RSI",
    "D": "Overbought or Below Trend",
    "Unclassified": "Unclassified",
}


@dataclass(frozen=True)
class ZoneParams:
    macro_sma_period: int = 200
    fast_ema_period: int = 9
    slow_ema_period: int = 21
    rsi_period: int = 14
    rsi_zone_a_max: float = 55
    rsi_zone_b_range: tuple[float, float] = (56, 65)
    rsi_zone_c_range: tuple[float, float] = (66, 71)
    rsi_zone_d_min: float = 72
    atr_period: int = 14
    atr_limit_multiplier: float = 0.25
    rvol_period: int = 20
    near_ema_pct: float = 0.02

    def __post_init__(self) -> None:
        if self.fast_ema_period >= self.slow_ema_period:
            raise ValueError("fast_ema_period must be < slow_ema_period")
        for name in ("macro_sma_period", "fast_ema_period", "slow_ema_period", "rsi_period", "atr_period", "rvol_period"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.near_ema_pct <= 0:
            raise ValueError("near_ema_pct must be > 0")
        b_lo, b_hi = self.rsi_zone_b_range
        c_lo, c_hi = self.rsi_zone_c_range
        if not (self.rsi_zone_a_max <= b_lo <= b_hi < c_lo <= c_hi < self.rsi_zone_d_min):
            raise ValueError(
                "RSI zone boundaries must be ordered: rsi_zone_a_max <= rsi_zone_b_range "
                "<= (gap allowed) < rsi_zone_c_range < rsi_zone_d_min"
            )

    @property
    def max_window(self) -> int:
        """The longest lookback any configured indicator needs."""
        return max(self.macro_sma_period, self.slow_ema_period, self.rsi_period, self.atr_period, self.rvol_period)


def _zone_for(rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams) -> str:
    """Just the zone code. Priority order, first match wins: D -> C -> B -> A -> Unclassified."""
    if rsi >= params.rsi_zone_d_min or (price < macro_sma and price < slow_ema):
        return "D"
    lo, hi = params.rsi_zone_c_range
    if lo <= rsi <= hi:
        return "C"
    lo, hi = params.rsi_zone_b_range
    if lo <= rsi <= hi and price > macro_sma:
        return "B"
    if rsi < params.rsi_zone_a_max and price > macro_sma:
        fast_near = fast_ema != 0 and abs(price - fast_ema) / fast_ema <= params.near_ema_pct
        slow_near = slow_ema != 0 and abs(price - slow_ema) / slow_ema <= params.near_ema_pct
        if fast_near or slow_near:
            return "A"
    return "Unclassified"


def _reason_for(
    zone: str, rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams
) -> str:
    """Factual, numeric reason text -- never a verb like buy/sell/hold/exit."""
    if zone == "D":
        if rsi >= params.rsi_zone_d_min:
            return f"RSI {rsi:.1f} >= {params.rsi_zone_d_min}"
        return (
            f"price {price:.2f} below {params.macro_sma_period} SMA ({macro_sma:.2f}) "
            f"and below {params.slow_ema_period} EMA ({slow_ema:.2f})"
        )
    if zone == "C":
        lo, hi = params.rsi_zone_c_range
        return f"RSI {rsi:.1f} within [{lo}, {hi}]"
    if zone == "B":
        lo, hi = params.rsi_zone_b_range
        return f"RSI {rsi:.1f} within [{lo}, {hi}], price above {params.macro_sma_period} SMA"
    if zone == "A":
        return (
            f"RSI {rsi:.1f} < {params.rsi_zone_a_max}, price above {params.macro_sma_period} SMA, "
            f"within {params.near_ema_pct:.0%} of an EMA"
        )
    return f"RSI {rsi:.1f}, price {price:.2f} matched no zone rule"


def classify_zone(
    rsi: float, price: float, macro_sma: float, fast_ema: float, slow_ema: float, params: ZoneParams
) -> tuple[str, str, str]:
    """Classify one instrument's latest bar. Returns (zone_code, zone_label, reason)."""
    zone = _zone_for(rsi, price, macro_sma, fast_ema, slow_ema, params)
    reason = _reason_for(zone, rsi, price, macro_sma, fast_ema, slow_ema, params)
    return zone, ZONE_LABELS[zone], reason
