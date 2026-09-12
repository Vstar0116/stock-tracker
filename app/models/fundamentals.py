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
    source: Mapped[str | None] = mapped_column(Text)
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
