"""Standalone timing check for the BS-V4 market-wide zone scan against real
data volume -- not a pytest test, a one-off report, matching the pattern
already used for the crossover feature's scan (scripts/bench_crossover_scan.py).

Run with: python -m scripts.bench_zone_scan
Requires a locally loaded database with realistic history (see RUN.md's
backfill instructions).
"""

import time

from app.db.session import SessionLocal
from app.services.zone_classifier import ZoneParams
from app.services.zone_loader import _load_wide_cached, _scan_cached, run_zone_scan
from app.services.screening import latest_trade_date

SCENARIOS = [
    ("defaults", ZoneParams()),
    ("shorter-macro", ZoneParams(macro_sma_period=50, fast_ema_period=9, slow_ema_period=21, rsi_period=14, atr_period=14, rvol_period=20)),
]


def main() -> None:
    db = SessionLocal()
    try:
        as_of = latest_trade_date(db)
        if as_of is None:
            print("No price data loaded -- nothing to benchmark.")
            return

        for label, params in SCENARIOS:
            _scan_cached.cache_clear()
            _load_wide_cached.cache_clear()

            t0 = time.perf_counter()
            result = run_zone_scan(db, params)
            t1 = time.perf_counter()

            result_cached = run_zone_scan(db, params)
            t2 = time.perf_counter()

            print(f"\n{label} (macro_sma_period={params.macro_sma_period}, as_of={as_of}):")
            print(f"  cold run (query+compute): {(t1 - t0) * 1000:.0f}ms")
            print(f"  warm run (cache hit):     {(t2 - t1) * 1000:.0f}ms")
            print(f"  evaluated={result.evaluated} matched={len(result.matches)} skipped={len(result.skipped)}")
            assert result_cached.cached is True
    finally:
        db.close()


if __name__ == "__main__":
    main()
