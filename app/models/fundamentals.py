from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Computed, Date, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Fundamental(Base):
    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("instrument_id", "as_of_date", name="uq_fundamentals_instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    pe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    roce: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # BS-GARP protocol fields (PEG, EPS growth, FCF) -- raw feed columns, manual
    # entry same as pe/pb/roce/debt_to_equity above.
    peg: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    eps_growth: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    fcf_per_share: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    # Derived (DB-computed, not entered): FCF per Share / Annual Diluted EPS,
    # as a percentage. Protocol 2.1 "Annual FCF Conversion" -- an earnings
    # quality gatekeeper, not a raw feed column, so it's computed once here
    # rather than re-derived (and risking drift) at every screen/query site.
    fcf_conversion: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4), Computed("fcf_per_share / NULLIF(eps_diluted, 0) * 100", persisted=True)
    )
    # Quality-ratio raw feed columns -- manual entry same as everything above.
    # Numeric(20, 4) matches market_cap: aggregate Rs-crore figures, not
    # per-share values.
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    equity: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    receivables: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    payables: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    inventory: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    sales: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    cogs: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # Derived (DB-computed, not entered) -- same fcf_conversion precedent.
    roe: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), Computed("net_profit / NULLIF(equity, 0) * 100", persisted=True))
    debtor_days: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4), Computed("receivables / NULLIF(sales, 0) * 365", persisted=True)
    )
    payable_days: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4), Computed("payables / NULLIF(cogs, 0) * 365", persisted=True)
    )
    inventory_days: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4), Computed("inventory / NULLIF(cogs, 0) * 365", persisted=True)
    )
    # Postgres generated columns can't reference other generated columns, so
    # this repeats the inventory_days/debtor_days/payable_days formulas
    # inline instead of adding them together.
    cash_conversion_cycle: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4),
        Computed(
            "(inventory / NULLIF(cogs, 0) * 365) + (receivables / NULLIF(sales, 0) * 365) "
            "- (payables / NULLIF(cogs, 0) * 365)",
            persisted=True,
        ),
    )
    working_capital_days: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 4), Computed("(receivables + inventory - payables) / NULLIF(sales, 0) * 365", persisted=True)
    )
    source: Mapped[str | None] = mapped_column(Text)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
