"""DB-backed persistence and lookup for uploaded portfolio-report PDFs.
app/services/portfolio_pdf.py (the pure regex parser) never touches the
database -- this module resolves its output against `instruments` and
persists it, same pure/DB split as app/services/crossover.py vs.
crossover_loader.py. Shared by app/api/portfolio_reports.py (upload, list)
and app/api/crossover.py (scan filtering), the way app/services/screening.py
is already shared across screens.py and crossover.py.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Instrument, PortfolioReport, PortfolioReportItem, User
from app.services.portfolio_pdf import ParsedReport


def _resolve_instruments(db: Session, tickers: list[str]) -> dict[str, Instrument]:
    """symbol -> Instrument, preferring NSE when a symbol exists on more than
    one exchange. `Instrument` is unique on (symbol, exchange) -- see
    app/models/instrument.py -- so e.g. a BSE scrip_id can coincide with an
    unrelated NSE symbol; these reports are NSE-style, so NSE wins whenever
    both are present. One IN query, then a single pass to apply that
    tie-break -- deterministic, no per-ticker round trips."""
    if not tickers:
        return {}
    rows = (
        db.execute(select(Instrument).where(Instrument.symbol.in_(tickers), Instrument.is_active)).scalars().all()
    )
    by_symbol: dict[str, Instrument] = {}
    for inst in rows:
        current = by_symbol.get(inst.symbol)
        if current is None or (current.exchange != "NSE" and inst.exchange == "NSE"):
            by_symbol[inst.symbol] = inst
    return by_symbol


def save_report(db: Session, user: User, filename: str, parsed: ParsedReport) -> PortfolioReport:
    """Persists the parsed rows and, for each, whichever Instrument its
    ticker resolved to (or none -- see PortfolioReportItem.instrument_id)."""
    resolved = _resolve_instruments(db, [row.ticker for row in parsed.rows])

    report = PortfolioReport(
        user_id=user.id,
        filename=filename,
        report_date=parsed.report_date,
        ticker_count=len(parsed.rows),
        matched_count=sum(1 for row in parsed.rows if row.ticker in resolved),
    )
    db.add(report)
    db.flush()  # assigns report.id for the child rows below

    for row in parsed.rows:
        inst = resolved.get(row.ticker)
        db.add(
            PortfolioReportItem(
                report_id=report.id,
                ticker=row.ticker,
                instrument_id=inst.id if inst else None,
                grp=row.group,
                score=row.score,
                pdf_price=row.price,
                zone=row.zone,
            )
        )
    db.commit()
    db.refresh(report)
    return report


def matched_instrument_ids(db: Session, report_id: int) -> frozenset[int]:
    """The instrument ids this report's tickers actually resolved to -- what
    ScanRequest.report_id restricts a crossover scan to (app/api/crossover.py)."""
    ids = db.execute(
        select(PortfolioReportItem.instrument_id).where(
            PortfolioReportItem.report_id == report_id, PortfolioReportItem.instrument_id.is_not(None)
        )
    ).scalars().all()
    return frozenset(ids)
