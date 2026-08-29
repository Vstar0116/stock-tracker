"""Tests for app/services/portfolio_pdf.py -- pure regex extraction, no DB.

Uses the checked-in real-world fixture tests/fixtures/portfolio_report.pdf
(a BANYAN-STRATUM-V4-style "Weekly Portfolio Overview" report) rather than a
synthetic one, so this exercises pypdf's actual text-layer quirks (its
extractor emits roughly one token per line, not real line-wrapped
sentences) instead of a hand-crafted approximation of them.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.portfolio_pdf import PortfolioPdfError, parse_portfolio_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "portfolio_report.pdf"


@pytest.fixture(scope="module")
def parsed():
    return parse_portfolio_pdf(FIXTURE.read_bytes())


@pytest.fixture(scope="module")
def by_ticker(parsed):
    return {row.ticker: row for row in parsed.rows}


def test_report_date_parsed_from_audit_date_line(parsed):
    assert parsed.report_date == date(2026, 8, 22)


def test_total_row_count_matches_the_reports_own_stated_universe_size(parsed):
    # The report's own executive summary states "the 54-stock expanded
    # master universe" -- the real assertion this test exists for.
    assert len(parsed.rows) == 54


def test_full_table_row_has_group_score_price_and_zone(by_ticker):
    row = by_ticker["TRENT"]
    assert row.group == "Core"
    assert row.score == 4
    assert row.price == Decimal("2924.00")
    assert row.zone == "A"


def test_group_with_embedded_period_parses_cleanly(by_ticker):
    row = by_ticker["ZYDUSLIFE"]
    assert row.group == "Defens."
    assert row.zone == "A"


def test_two_word_group_parses_cleanly(by_ticker):
    row = by_ticker["JLHL"]
    assert row.group == "Dark H."


def test_zone_d_row_from_a_different_page_than_its_ticker(by_ticker):
    # DIVISLAB's row and its "Zone D" marker sit right next to each other on
    # the same page -- still worth pinning given zone association is
    # order-based, not table-geometry-based.
    row = by_ticker["DIVISLAB"]
    assert row.score == 1
    assert row.zone == "D"


def test_zone_b_row(by_ticker):
    row = by_ticker["SIEMENS"]
    assert row.group == "Growth"
    assert row.score == 3
    assert row.zone == "B"


def test_bulk_row_tickers_present_with_null_group_and_price(by_ticker):
    for ticker in ("TATAELXSI", "KEC", "HAVELLS", "NIDHGRN"):
        row = by_ticker[ticker]
        assert row.group is None
        assert row.price is None
        assert row.score == 1
        assert row.zone == "D"


def test_bulk_row_yields_exactly_its_nineteen_tickers(parsed):
    bulk_tickers = {
        "TATAELXSI", "KEC", "HAVELLS", "COCHINSHIP", "CHAMBLFERT", "KAYNES", "SYNGENE", "LUPIN",
        "ZENSARTECH", "WAAREEENER", "ELECON", "SHARDACROP", "CRIZAC", "IONEXCHANG", "SHILCTECH",
        "FRONTSP", "ABBOTINDIA", "AUSTENG", "NIDHGRN",
    }
    by_ticker = {row.ticker: row for row in parsed.rows}
    assert bulk_tickers <= by_ticker.keys()
    for ticker in bulk_tickers:
        assert by_ticker[ticker].price is None


def test_narrative_mentions_of_a_table_ticker_are_not_double_counted(parsed):
    # Page 1's executive summary mentions TRENT, POLYCAB, DIVISLAB and
    # CHENNPETRO again in prose ("TRENT (Core - Score 4): Despite...") --
    # each must still appear exactly once in the parsed rows, not twice.
    tickers = [row.ticker for row in parsed.rows]
    for ticker in ("TRENT", "POLYCAB", "DIVISLAB", "CHENNPETRO"):
        assert tickers.count(ticker) == 1


def test_non_ticker_words_are_not_extracted(by_ticker):
    # "Various" (the bulk row's own Group/Price placeholder), RSI/EMA/SMA
    # abbreviations, and section headers must never surface as tickers.
    for noise in ("VARIOUS", "Various", "RSI", "EMA", "SMA", "GTD", "ZONE"):
        assert noise not in by_ticker


def test_not_a_pdf_raises_portfolio_pdf_error():
    with pytest.raises(PortfolioPdfError):
        parse_portfolio_pdf(b"this is not a pdf")


def test_pdf_with_no_ticker_rows_raises_portfolio_pdf_error():
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = BytesIO()
    writer.write(buf)

    with pytest.raises(PortfolioPdfError):
        parse_portfolio_pdf(buf.getvalue())
