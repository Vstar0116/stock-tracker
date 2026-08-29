"""Upload a weekly portfolio-report PDF, list past uploads, and turn one
into a real watchlist. Parsing is app/services/portfolio_pdf.py (pure, no
LLM -- see that module's docstring for why this doesn't fall under
CLAUDE.md's document-summarisation exclusion); persistence and symbol
resolution are app/services/portfolio_reports.py. Scan-time filtering by
report_id lives on POST /api/scans/crossover (app/api/crossover.py), not here.
"""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Pagination, get_current_user, get_owned_report, pagination
from app.db.session import get_db
from app.models import Instrument, PortfolioReport, PortfolioReportItem, User, Watchlist, WatchlistItem
from app.rate_limit import RateLimiter
from app.schemas.common import Page
from app.schemas.portfolio_report import PortfolioReportItemOut, PortfolioReportOut, PortfolioReportSummary
from app.schemas.watchlist import WatchlistOut
from app.services.portfolio_pdf import PortfolioPdfError, parse_portfolio_pdf
from app.services.portfolio_reports import matched_instrument_ids, save_report

router = APIRouter(prefix="/api/portfolio-reports", tags=["portfolio-reports"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# Not an external-cost concern like nl_screen_daily_limiter (screens.py) --
# this never leaves our own process. It bounds worst-case PDF-parsing CPU
# and row-write volume from one account, same spirit as every other
# user-triggered write path here.
portfolio_upload_limiter = RateLimiter(
    key_prefix="portfolio_report:user",
    max_requests=20,
    window_seconds=3600,
    message="too many report uploads -- try again in a while",
)


def _load_report_out(db: Session, report: PortfolioReport) -> PortfolioReportOut:
    rows = db.execute(
        select(PortfolioReportItem, Instrument.symbol)
        .outerjoin(Instrument, Instrument.id == PortfolioReportItem.instrument_id)
        .where(PortfolioReportItem.report_id == report.id)
        .order_by(PortfolioReportItem.id)
    ).all()
    items = [
        PortfolioReportItemOut(
            ticker=item.ticker,
            instrument_id=item.instrument_id,
            matched=item.instrument_id is not None,
            symbol=symbol,
            grp=item.grp,
            score=item.score,
            pdf_price=float(item.pdf_price) if item.pdf_price is not None else None,
            zone=item.zone,
        )
        for item, symbol in rows
    ]
    return PortfolioReportOut(
        id=report.id,
        filename=report.filename,
        report_date=report.report_date,
        uploaded_at=report.uploaded_at,
        ticker_count=report.ticker_count,
        matched_count=report.matched_count,
        items=items,
    )


@router.post("", response_model=PortfolioReportOut, status_code=status.HTTP_201_CREATED)
async def upload_report(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioReportOut:
    portfolio_upload_limiter.check(str(current_user.id))

    filename = file.filename or "report.pdf"
    if file.content_type != "application/pdf" and not filename.lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "only PDF files are accepted")

    # Read one byte past the cap so a file exactly at the limit isn't
    # mistaken for one that got truncated by the cap itself.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large (max 5 MB)")

    try:
        parsed = parse_portfolio_pdf(data)
    except PortfolioPdfError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    report = save_report(db, current_user, filename[:255], parsed)
    return _load_report_out(db, report)


@router.get("", response_model=Page[PortfolioReportSummary])
def list_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: Pagination = Depends(pagination),
) -> Page[PortfolioReportSummary]:
    total = db.execute(
        select(func.count()).select_from(PortfolioReport).where(PortfolioReport.user_id == current_user.id)
    ).scalar_one()
    rows = (
        db.execute(
            select(PortfolioReport)
            .where(PortfolioReport.user_id == current_user.id)
            .order_by(PortfolioReport.uploaded_at.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
        .scalars()
        .all()
    )
    return Page(
        items=[PortfolioReportSummary.model_validate(r) for r in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/{report_id}", response_model=PortfolioReportOut)
def get_report(report: PortfolioReport = Depends(get_owned_report), db: Session = Depends(get_db)) -> PortfolioReportOut:
    return _load_report_out(db, report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report: PortfolioReport = Depends(get_owned_report), db: Session = Depends(get_db)) -> None:
    db.delete(report)
    db.commit()


@router.post("/{report_id}/watchlist", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def save_report_as_watchlist(
    report: PortfolioReport = Depends(get_owned_report),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WatchlistOut:
    instrument_ids = matched_instrument_ids(db, report.id)
    if not instrument_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "no matched tickers in this report to save")

    name = f"{report.filename} ({report.report_date})" if report.report_date else report.filename
    watchlist = Watchlist(user_id=current_user.id, name=name[:200])
    db.add(watchlist)
    db.flush()
    for instrument_id in instrument_ids:
        db.add(WatchlistItem(watchlist_id=watchlist.id, instrument_id=instrument_id))
    db.commit()
    db.refresh(watchlist)
    return WatchlistOut.model_validate(watchlist)
