from datetime import date

from pydantic import BaseModel


class InstrumentOut(BaseModel):
    id: int
    symbol: str
    exchange: str
    company_name: str
    series: str | None
    sector: str | None
    industry: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class IndicatorOut(BaseModel):
    trade_date: date
    sma_20: float | None
    sma_50: float | None
    sma_100: float | None
    sma_200: float | None
    ema_20: float | None
    ema_50: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    atr_14: float | None
    volume_sma_20: float | None
    high_52w: float | None
    low_52w: float | None

    model_config = {"from_attributes": True}


class InstrumentDetail(InstrumentOut):
    isin: str | None
    listed_date: date | None
    latest_indicators: IndicatorOut | None
    latest_trade_date: date | None
    latest_close: float | None
    day_change_abs: float | None
    day_change_pct: float | None


class PriceOut(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: int

    model_config = {"from_attributes": True}
