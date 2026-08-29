"""Deterministic ticker extraction from a weekly portfolio-report PDF (e.g. a
BANYAN-STRATUM-V4 style "Weekly Portfolio Overview"), so Custom Scan can
restrict a crossover scan to a report's own universe.

CLAUDE.md rules out "document summarisation / RAG over filings or news" and
requires numeric questions be answered by SQL, never an LLM guessing. Neither
applies here: this module never summarises or interprets anything, and
involves no LLM at all -- it only regex-matches the report's own
already-computed per-row fields (ticker, group, score, price, zone) out of
the PDF's text layer. Symbol -> Instrument resolution and any DB access live
in app/services/portfolio_report_loader.py, not here (same pure-calc /
loader split as app/services/crossover.py + crossover_loader.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

_TICKER = r"[A-Z][A-Z0-9&.\-]{1,19}"
_GROUP = r"Core|Growth|Defens\.?|Dark\s+H\.?"

# A table row's own head, as pypdf's text layer renders it once whitespace is
# collapsed to single spaces: "<TICKER> <Group> <1-4 score> <price, 2dp>".
_ROW_RE = re.compile(rf"(?P<ticker>{_TICKER})\s+(?P<group>{_GROUP})\s+(?P<score>[1-4])\s+(?P<price>[\d,]+\.\d{{2}})\b")

# The report's "everything else" row: a comma-separated ticker list whose
# Group and Price columns both read "Various" instead of a per-ticker value.
# Anchoring on the literal "Various <score> Various" tail (not just "a run of
# ALL-CAPS tokens") is what keeps this from also matching unrelated ALL-CAPS
# abbreviations (RSI, EMA, SMA, ...) that appear elsewhere in the narrative.
_BULK_RE = re.compile(rf"(?P<tickers>(?:{_TICKER}\s*,\s*)+{_TICKER})\s+Various\s+(?P<score>\d+)\s+Various\b")

_ZONE_RE = re.compile(r"Zone\s+([A-D])\b")
_DATE_RE = re.compile(r"Audit Date:\s*([A-Za-z]+,\s*[A-Za-z]+\s+\d{1,2},\s*\d{4})")

# How far past a row to look for *that row's own* "Zone X" marker. Bounded by
# the next row's start when there is one; this fallback only matters for the
# very last row in the document.
_ZONE_SEARCH_WINDOW = 400


@dataclass(frozen=True)
class PdfRow:
    ticker: str
    group: str | None
    score: int | None
    price: Decimal | None
    zone: str | None


@dataclass(frozen=True)
class ParsedReport:
    report_date: Date | None
    rows: list[PdfRow]


class PortfolioPdfError(Exception):
    """Not a readable PDF, or contained no recognisable ticker rows."""


def parse_portfolio_pdf(data: bytes) -> ParsedReport:
    try:
        reader = PdfReader(BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise PortfolioPdfError("could not read this file as a PDF") from exc

    normalized = re.sub(r"\s+", " ", text).strip()

    rows: list[PdfRow] = []
    seen: set[str] = set()
    for row in _extract_table_rows(normalized) + _extract_bulk_row(normalized):
        if row.ticker in seen:
            continue
        seen.add(row.ticker)
        rows.append(row)

    if not rows:
        raise PortfolioPdfError("no ticker rows found in this PDF")

    return ParsedReport(report_date=_extract_report_date(normalized), rows=rows)


def _zone_after(text: str, start: int, end: int) -> str | None:
    match = _ZONE_RE.search(text, start, end)
    return match.group(1) if match else None


def _extract_table_rows(text: str) -> list[PdfRow]:
    matches = list(_ROW_RE.finditer(text))
    rows = []
    for i, m in enumerate(matches):
        window_end = matches[i + 1].start() if i + 1 < len(matches) else m.end() + _ZONE_SEARCH_WINDOW
        rows.append(
            PdfRow(
                ticker=m.group("ticker"),
                group=_normalize_group(m.group("group")),
                score=int(m.group("score")),
                price=_parse_price(m.group("price")),
                zone=_zone_after(text, m.end(), window_end),
            )
        )
    return rows


def _extract_bulk_row(text: str) -> list[PdfRow]:
    m = _BULK_RE.search(text)
    if m is None:
        return []
    score = int(m.group("score"))
    zone = _zone_after(text, m.end(), m.end() + _ZONE_SEARCH_WINDOW)
    tickers = [t.strip() for t in m.group("tickers").split(",") if t.strip()]
    return [PdfRow(ticker=t, group=None, score=score, price=None, zone=zone) for t in tickers]


def _normalize_group(raw: str) -> str:
    if raw.startswith("Dark"):
        return "Dark H."
    if raw.startswith("Defens"):
        return "Defens."
    return raw


def _parse_price(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _extract_report_date(text: str) -> Date | None:
    m = _DATE_RE.search(text)
    if m is None:
        return None
    try:
        return datetime.strptime(m.group(1), "%A, %B %d, %Y").date()
    except ValueError:
        return None
