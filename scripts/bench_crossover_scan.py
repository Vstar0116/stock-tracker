"""Standalone timing check for the market-wide crossover scan against real
data volume -- not a pytest test, a one-off report. The spec's "a few
seconds" promise to the user should be measured here, not assumed.

Run with: python -m scripts.bench_crossover_scan
Requires a locally loaded database with realistic history (see RUN.md's
backfill instructions) -- results against a handful of seeded rows in the
test suite don't tell you anything about the real ~7,500-instrument cost.
"""

import time

from app.db.session import SessionLocal
from app.services.crossover_loader import _load_wide_cached, _scan_cached, resolve_window, run_scan
from app.services.crossover import warmup_bars

SCENARIOS = [
    (9, 21, "ema"),
    (20, 50, "sma"),
    (50, 200, "sma"),
]


def main() -> None:
    db = SessionLocal()
    try:
        for fast, slow, ma_type in SCENARIOS:
            _scan_cached.cache_clear()
            _load_wide_cached.cache_clear()

            n_bars = warmup_bars(slow, ma_type)
            t0 = time.perf_counter()
            cutoff, as_of = resolve_window(n_bars)
            t1 = time.perf_counter()

            result = run_scan(db, fast, slow, ma_type, "any")
            t2 = time.perf_counter()

            result_cached = run_scan(db, fast, slow, ma_type, "any")
            t3 = time.perf_counter()

            print(f"\n{ma_type.upper()} {fast}/{slow} (warmup={n_bars} bars, window={cutoff}..{as_of}):")
            print(f"  resolve_window:        {(t1 - t0) * 1000:.0f}ms")
            print(f"  cold run (query+compute): {(t2 - t1) * 1000:.0f}ms")
            print(f"  warm run (cache hit):   {(t3 - t2) * 1000:.0f}ms")
            print(f"  evaluated={result.evaluated} matched={len(result.matches)} "
                  f"stale={result.skipped_stale} short_history={result.skipped_insufficient_history}")
            assert result_cached.cached is True
    finally:
        db.close()


if __name__ == "__main__":
    main()
