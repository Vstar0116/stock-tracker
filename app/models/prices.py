from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("instrument_id", "trade_date", name="uq_daily_prices_instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    adjusted_close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)


Index("ix_daily_prices_instrument_trade_date", DailyPrice.instrument_id, DailyPrice.trade_date.desc())


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("instrument_id", "ex_date", "action_type", name="uq_corporate_actions_instrument_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    ratio_from: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    ratio_to: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    raw_description: Mapped[str | None] = mapped_column(Text)


class Indicator(Base):
    __tablename__ = "indicators"
    __table_args__ = (UniqueConstraint("instrument_id", "trade_date", name="uq_indicators_instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    sma_20: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    sma_50: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    sma_100: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    sma_200: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    ema_20: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    ema_50: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    rsi_14: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    macd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    macd_histogram: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    atr_14: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume_sma_20: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    high_52w: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low_52w: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))


Index("ix_indicators_instrument_trade_date", Indicator.instrument_id, Indicator.trade_date.desc())
