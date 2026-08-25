"""Pins BSE's ListofScripData response shape and the upsert of `bse_scrip_code`,
using a real row captured from api.bseindia.com on 2026-08-25. TradingView's
BSE: chart prefix only resolves the numeric scrip code (SCRIP_CD), not BSE's
text scrip_id (which we store as `symbol`) -- losing SCRIP_CD at ingestion is
exactly what broke the chart for every BSE-listed instrument.

Run with: pytest tests/test_ingest_instruments.py -v
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.db.session import engine
from app.jobs.ingest_instruments import _bse_row_to_dict, upsert_instruments
from app.models import Instrument

REAL_BSE_ROW = {
    "SCRIP_CD": "500002",
    "Scrip_Name": "ABB India Ltd",
    "Status": "Active",
    "GROUP": "A",
    "FACE_VALUE": "2.00",
    "ISIN_NUMBER": "INE117A01022",
    "INDUSTRY": None,
    "scrip_id": "ABB",
    "Segment": "Equity",
    "NSURL": "https://www.bseindia.com/stock-share-price/abb-india-ltd/abb/500002/",
    "Issuer_Name": "ABB India Limited",
    "Mktcap": "158973.66",
}


def test_bse_row_to_dict_captures_numeric_scrip_code():
    row = _bse_row_to_dict(REAL_BSE_ROW)
    assert row["symbol"] == "ABB"
    assert row["bse_scrip_code"] == "500002"
    assert row["company_name"] == "ABB India Limited"
    assert row["isin"] == "INE117A01022"


def test_bse_row_to_dict_missing_scrip_code_is_none_not_empty_string():
    row = _bse_row_to_dict({**REAL_BSE_ROW, "SCRIP_CD": ""})
    assert row["bse_scrip_code"] is None


@pytest.fixture()
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def test_upsert_instruments_persists_bse_scrip_code(db):
    now = datetime.now(timezone.utc)
    row = _bse_row_to_dict(REAL_BSE_ROW)
    upsert_instruments(db, "BSE", [row], now)
    db.flush()

    saved = db.query(Instrument).filter_by(symbol="ABB", exchange="BSE").one()
    assert saved.bse_scrip_code == "500002"


def test_upsert_instruments_updates_bse_scrip_code_on_conflict(db):
    now = datetime.now(timezone.utc)
    upsert_instruments(db, "BSE", [_bse_row_to_dict(REAL_BSE_ROW)], now)
    db.flush()

    reissued = {**REAL_BSE_ROW, "SCRIP_CD": "500099"}
    upsert_instruments(db, "BSE", [_bse_row_to_dict(reissued)], now)
    db.flush()

    saved = db.query(Instrument).filter_by(symbol="ABB", exchange="BSE").one()
    assert saved.bse_scrip_code == "500099"


def test_upsert_instruments_nse_bse_scrip_code_is_none(db):
    now = datetime.now(timezone.utc)
    nse_row = {
        "symbol": "FDC",
        "bse_scrip_code": None,
        "isin": None,
        "company_name": "FDC Limited",
        "series": "EQ",
        "sector": None,
        "industry": None,
        "listed_date": None,
    }
    upsert_instruments(db, "NSE", [nse_row], now)
    db.flush()

    saved = db.query(Instrument).filter_by(symbol="FDC", exchange="NSE").one()
    assert saved.bse_scrip_code is None
