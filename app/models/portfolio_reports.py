from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PortfolioReport(Base):
    """One uploaded portfolio-report PDF, parsed via
    app/services/portfolio_pdf.py. The PDF bytes themselves are never
    stored -- only the rows extracted from it (PortfolioReportItem below)."""

    __tablename__ = "portfolio_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ticker_count: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_portfolio_reports_user_id", PortfolioReport.user_id)


class PortfolioReportItem(Base):
    """One ticker row parsed from a PortfolioReport. instrument_id is
    nullable -- null means the report named a ticker our instruments table
    doesn't track, kept here (rather than dropped) so "what didn't match"
    survives a page reload instead of only existing in the upload response."""

    __tablename__ = "portfolio_report_items"
    __table_args__ = (UniqueConstraint("report_id", "ticker", name="uq_portfolio_report_items_report_ticker"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("portfolio_reports.id", ondelete="CASCADE"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="SET NULL"))
    grp: Mapped[str | None] = mapped_column(String(20))
    score: Mapped[int | None] = mapped_column(Integer)
    pdf_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    zone: Mapped[str | None] = mapped_column(String(1))


Index("ix_portfolio_report_items_report_id", PortfolioReportItem.report_id)
Index("ix_portfolio_report_items_instrument_id", PortfolioReportItem.instrument_id)
