"""Pins the NSE/BSE UDiFF bhavcopy column names parse_bhavcopy() depends on,
using a real header+rows captured from nsearchives.nseindia.com on 2026-08-20.
If NSE/BSE renames or drops a required column, this fails instead of the job
silently upserting nothing while still reporting "success".

Run with: pytest tests/test_ingest_prices.py -v
"""

from decimal import Decimal

import pytest

from app.jobs.ingest_prices import parse_bhavcopy

REAL_HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)
REAL_ROW = (
    "2026-08-19,2026-08-19,CM,NSE,STK,19078,IN0020200104,SGBJUN28,GB,,,,,"
    "2.5%GOLDBONDS2028SR-III,15199.99,15199.99,15062.10,15140.65,15150.00,15222.45,,"
    "15135.29,,,81,1223724.19,26,F1,1,,,,,"
)


def test_parse_bhavcopy_extracts_expected_fields():
    raw = f"{REAL_HEADER}\n{REAL_ROW}\n".encode("utf-8-sig")
    rows = parse_bhavcopy(raw)
    assert rows == [
        {
            "symbol": "SGBJUN28",
            "series": "GB",
            "open": Decimal("15199.99"),
            "high": Decimal("15199.99"),
            "low": Decimal("15062.10"),
            "close": Decimal("15140.65"),
            "volume": 81,
        }
    ]


def test_parse_bhavcopy_raises_loudly_when_columns_change():
    """Simulates NSE renaming TckrSymb -- every row would fail to parse.
    Must raise, not silently return an empty list (which the job would log
    as a successful 0-row load)."""
    broken_header = REAL_HEADER.replace("TckrSymb", "Symbol")
    raw = f"{broken_header}\n{REAL_ROW}\n".encode("utf-8-sig")
    with pytest.raises(ValueError, match="expected columns may have changed"):
        parse_bhavcopy(raw)


def test_parse_bhavcopy_skips_only_the_bad_row():
    good = REAL_ROW
    bad = REAL_ROW.replace("15140.65", "")  # blank close price, e.g. a halted security
    raw = f"{REAL_HEADER}\n{good}\n{bad}\n".encode("utf-8-sig")
    rows = parse_bhavcopy(raw)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SGBJUN28"
