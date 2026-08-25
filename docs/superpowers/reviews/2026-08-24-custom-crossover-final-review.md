# Final whole-branch review — Custom MA Crossover Indicator

**Branch:** worktree-custom-crossover-indicator
**Range:** 6b9866e..80884ce (11 commits, all 8 tasks)
**Reviewer model:** Opus
**Plan:** docs/superpowers/plans/2026-08-24-custom-crossover-indicator.md
**Spec:** docs/superpowers/specs/2026-08-24-custom-crossover-indicator-design.md

---

## Strengths

**Global constraints held across every layer.** Checked each one end to end rather than per-task:

- **No migration, no schema change** — confirmed against the full file list; nothing under `alembic/`, and `indicators`/`ScreenRule`/`compute_indicators.py` are untouched. `app/main.py` gains only an import and an `include_router`.
- **Period validation does not drift.** All five enforcement points are semantically identical: `app/services/crossover.py:303-310` (`validate_periods`), `app/schemas/crossover.py:238-247` (`ScanRequest` + `model_validator`), `app/api/crossover.py:96-99` (GET, explicit try/except → 422), `frontend/src/pages/StockDetailPage.tsx:3008` and `frontend/src/pages/CustomScanPage.tsx:2873` (byte-identical `invalid` expression). Empty input coerces to `0` and is caught by `fastNum < 1` in both components. This was the highest-risk cross-task item and it's clean.
- **Both MAs share `ma_type`** — structurally impossible to mix; a single `ma_type` parameter threads through every signature.
- **`adjusted_close` only** — the loader SQL selects it explicitly, `compute_crossover` reads only that column, and `load_price_history` supplies it. Raw `close` never appears in the compute path.
- **`as_of` is market-wide** — `latest_trade_date(db)` (`MAX(trade_date)`), never per-instrument.
- **`sma()`/`ema()` reused**, not reimplemented — and notably the spec's draft `_ma()` would have *diverged* from `indicators.ema()` (the spec draft used `min_periods=window` on `ewm`; the real `ema()` uses an expanding non-NaN count). The implementer correctly reused the real functions instead of transcribing the spec's pseudocode. That's the kind of judgment call worth calling out.
- **No advice language.** "Crossed above / Crossed below", "No crossover on the latest bar", "No stocks currently match this crossover." Both directions use the same neutral `tag-accent` styling — no red/green buy-sell coding. The global disclaimer in `AppShell.tsx:165` covers `/scan` since it renders inside the shell.

**Architecture.** The calc → loader → API separation is genuinely clean, and `_crossing_masks` (`app/services/crossover.py:332-338`) is the right call: the single-instrument and market-wide paths share one definition of "crossing" and a parity test enforces agreement. That's the duplication risk the plan named, and it was closed properly rather than left to convention.

**Security.** Every query is parameterized (`:n`, `:cutoff`, `.in_()`, ORM). `ma_type` is `Literal`-validated at the boundary and never reaches SQL in any form. No f-string interpolation anywhere in `crossover_loader.py`. No injection surface.

**Float discipline.** `float64` is used only for derived indicator math and never written back to a NUMERIC column — consistent with the documented convention in `app/services/indicators.py:10-13`.

**Honest measurement.** Task 8 reported a target miss rather than quietly accepting it, ran twice to rule out noise, and correctly diagnosed the cause. The mid-branch fix `fc91b30` (query window must be ≥ staleness tolerance) is a real bug caught and fixed with a proper regression test.

---

## Issues

### Critical (Must Fix)

**1. Six of the branch's new backend tests fail against a database with real data — the exact environment Task 8 mandates.**

`tests/test_crossover_loader.py` (5 failures) and `tests/test_api.py:591` `TestCrossoverScan::test_finds_matches_across_instruments` (1 failure).

Ran the suite in this worktree against the local Postgres:

```
6 failed, 168 passed, 1 warning in 32.25s
```

(168 + 6 = the 174 the reports claim — the count is right, but they no longer all pass.)

Root cause is one thing, not six: these tests assume `daily_prices` and `instruments` contain **only** their own seeded rows. But `resolve_window`, `load_wide`, and `SELECT COUNT(*) FROM instruments WHERE is_active` are all whole-table queries with no instrument filter. Task 8's brief required backfilling the real market data into the same Postgres container — and doing so broke Task 3's and Task 5's tests. Representative failures:

```
tests/test_crossover_loader.py:64
E  assert datetime.date(2026, 8, 20) == date(2026,1,1) + timedelta(days=9)

tests/test_crossover_loader.py:126
E  assert 49569 in Index([1, 2, 3, ...], length=2648)

tests/test_api.py:619
E  assert 'XOVR2' in {'08GPG', '08MPD', '11AQD', ...}
```

This is precisely a cross-task collision that no per-task review could see: Task 3 was green when the DB was empty, Task 8 made the DB non-empty by design, and nobody re-ran the suite afterward. It also silently hollowed out two tests that still "pass": `test_direction_filter_excludes_non_matching_signals` now passes because its instrument is absent for the wrong reason (dates outside the market window), and `test_repeat_call_same_as_of_is_a_cache_hit` no longer exercises any seeded data at all.

**Fix:** make the tests independent of ambient data. The cheapest route that keeps the existing structure is to seed the throwaway instruments on trade dates anchored to the *real* `as_of` (`latest_trade_date(db)` minus N days) instead of a hardcoded `2026-01-01`, and to assert on relative counts (`gap_id in matches`, `skipped_stale` delta) rather than absolutes like `result.evaluated == 2`. Alternatively, give the loader an optional instrument-id filter used only by tests — but that adds production surface for test convenience, so prefer the anchoring approach. Either way, the suite must be green against a realistically-populated DB, because that is the only DB this feature is ever meant to run on.

### Important (Should Fix)

**2. `app/api/crossover.py:86-91, 103` — the inner join silently discards legitimate matches, defeating the forward-fill tolerance built in Task 3.**

```python
select(Instrument, DailyPrice.adjusted_close)
.join(DailyPrice, (DailyPrice.instrument_id == Instrument.id) & (DailyPrice.trade_date == result.as_of))
...
if instrument_id in rows
```

`load_wide` deliberately forward-fills gaps up to `STALE_TOLERANCE_DAYS` so an instrument that didn't trade on `as_of` (halt, suspension, illiquid smallcap with no bhavcopy row) is still scored — that's the documented contract in the spec's *"Most recent trading day"* section. The API then re-filters those very instruments out, because an inner join on `trade_date == as_of` finds no row for them. The scan says "matched", the response says nothing, and `stats` accounts for it nowhere: `skipped_stale` doesn't count them (they weren't stale) and `matched` is computed *after* the drop, so the number is internally consistent and externally wrong.

This is not merely an untested defensive branch (as the deferred Task 5 note framed it) — it is a live behavior that cancels a Task 3 feature. Measured against the real DB:

```
as_of 2026-08-20  matches 141
with as_of row: 139   silently dropped: 2
```

1.4% of matches vanish on SMA 20/50, and the rate scales with however many instruments are mid-gap on any given day.

**Fix:** make it a left outer join from `Instrument`, so a missing price row yields `latest_close=None` and the match survives. Drop the `if instrument_id in rows` guard down to a genuine can't-happen case (instrument deleted between scan and hydration) and count those separately if you want to keep the guard at all.

**3. `app/services/crossover_loader.py:466` — `@lru_cache(maxsize=32)` on `_load_wide_cached` retains up to 32 full market frames.**

Each cached entry is a dense `trade_date × instrument_id` float64 frame. The everyday case (EMA 9/21, 250 bars × 7,528 instruments) is ~15 MB; the spec's own worst case (`slow=400` EMA → 2,400 bars) is ~145 MB, and the spec explicitly flags that single frame as "the number to sanity-check on the target box." Thirty-two of them is not a number anyone sanity-checked. Entries are keyed `(n_bars, as_of)`, so yesterday's frames are never proactively evicted — they just sit there until 32 distinct keys accumulate, which happens naturally as the user varies `slow` across days.

The plan specified `maxsize=32`, so this is partly a plan defect rather than an implementation one — but a long-running uvicorn process for 4-5 users has no reason to hold more than a couple of these.

**Fix:** `maxsize=2` (or 4). The cache's value is the *repeat-with-same-params* case, which one or two entries fully covers; the `_scan_cached(maxsize=64)` layer above it is cheap and can stay.

### Minor (Nice to Have)

**4. Validation rule is triplicated on the frontend.** The `invalid` expression is copy-pasted verbatim into `StockDetailPage.tsx:3008` and `CustomScanPage.tsx:2873`, with `400` hardcoded in both — a third copy of `MAX_PERIOD`. No drift today (checked character by character), but three independent copies of one rule is exactly how drift starts. Extract `isValidPeriodPair(fast, slow)` and a `MAX_PERIOD` constant into `frontend/src/lib/`.

**5. `app/services/crossover_loader.py:538-542` — `elapsed_ms` under-reports what the user waits for.** The timer starts *after* `latest_trade_date(db)` and stops before the API's match-hydration query. On real data `latest_trade_date` alone is ~90 ms (it's the bulk of the "warm run" number in the spec). A warm scan reports single-digit `elapsed_ms` to the UI while the request actually takes ~100 ms+. Move `t0` above the `latest_trade_date` call.

**6. `CustomScanPage.tsx:2929-2934` — the stats line reads as a contradiction.** It renders `"141 of 7528 evaluated, 83 stale, 792 short on history"`, but `evaluated` is `total_active` and *already includes* the 83 and 792. The known naming nit becomes user-visible here. Either relabel to "of 7528 instruments" or subtract the skips before display.

**7. Spec/implementation drift on the GET endpoint.** The spec (`Architecture → API endpoints`) specifies `optional from / to to trim the payload`; the plan silently dropped them and the implementation has none. Harmless today (~1,250 points ≈ 120 KB), and it becomes relevant only in sub-project B when this feeds a chart — but the spec now describes an endpoint that doesn't exist. Either implement the params or amend the spec.

**8. `crossover_loader.py:493` — `wide.iloc[-1]` raises `IndexError` on an empty frame → 500.** Reachable only if every price row belongs to an inactive instrument (`latest_trade_date` non-null, `load_wide` empty). Very unlikely, but it's a 500 rather than the 404 the surrounding code produces for "no data". One `if wide.empty:` guard raising the same `ValueError` closes it.

**9. `crossover_loader.py:416` — `Direction = str` with the real values in a comment,** while `app/schemas/crossover.py:220` has a proper `Literal`. The loader's `run_scan` accepts any string; a typo'd direction silently returns zero matches instead of erroring. Import the `Literal` alias.

**10. `ScanRequest` advertises no upper bound in OpenAPI.** `Field(ge=1)` on both periods with the ceiling enforced only in the `model_validator` — correct at runtime, but the generated schema tells clients `slow` is unbounded. Add `le=MAX_PERIOD` for documentation parity.

**Not an issue, for the record:** the `frontend/package-lock.json` churn in this diff (moving `react-router-dom` and its transitive deps out of `dev`) is a *correction*, not an unrelated change. `frontend/package.json` has listed it under `dependencies` since the initial commit and is untouched by this branch — the lockfile was simply stale and got regenerated. Fine to keep.

---

## Recommendations

1. **Fix issue 1 before anything else, then re-run the full suite against the backfilled DB and record the result.** The branch currently cannot demonstrate a green suite in its own target environment, and every downstream claim about correctness rests on that suite.
2. **Add a test for issue 2 while fixing it** — seed an instrument whose last bar is `as_of - 2` days with a crossover on its filled last row, assert it appears in the response with `latest_close: null`. That single test covers the deferred Task 5 note and the real bug at once.
3. **Consider a test-isolation convention for whole-table analytical queries generally.** This feature is the first in the codebase whose service layer queries the entire market unfiltered; the Screener presumably scopes by rule. Whatever pattern lands for issue 1 is worth writing down, because sub-project C (backtesting) will hit the same wall.
4. **On the deferred index:** the spec's Task 8 section is adequate as a decision record — it has the numbers, both runs, the diagnosis, and the named next step. Add one line stating the target hardware/Postgres config, since a 4.4s scan is a very different decision on a laptop container versus the box this will actually run on. The missing per-scenario counts for Run 2 don't matter.
5. **Two accepted-by-design deviations worth noting explicitly rather than discovering later:** CLAUDE.md principle #2 says heavy work happens in scheduled jobs, and a 2-6 s synchronous scan bends that. The spec justifies it (separate page, explicit button, no confusion with the instant Screener) and the routes are sync `def` so FastAPI threadpools them rather than blocking the event loop — this is fine, but it's the first exception in the codebase and should be a conscious precedent. Relatedly, `lru_cache` doesn't dedupe *concurrent* misses, so two simultaneous cold scans compute (and hold) two frames at once; harmless at 4-5 users, relevant if that assumption changes.

---

## Assessment

**Ready to merge?** With fixes

**Reasoning:** The design is sound and every binding global constraint holds cleanly across all five layers — the period-validation rule was specifically checked for drift and found none, which was the main cross-task risk. But six new tests fail against a realistically-populated database (the environment Task 8 itself creates), and the scan endpoint's inner join silently discards ~1.4% of legitimate matches in a way that cancels the forward-fill tolerance the loader was built to provide. Both are cross-task integration defects invisible to per-task review, and both must be fixed before merge; the `lru_cache` sizing is a should-fix that takes one character.

---

## Disposition — RESOLVED, ready to merge

Critical finding 1 and Important findings 2–3 were fixed in one scoped pass, commit `a123a0e`:

1. **Test isolation** — the 7 affected tests in `tests/test_crossover_loader.py` and `tests/test_api.py` now anchor seeded rows to the real current market calendar (new `_recent_trade_dates` helper) and assert relative/own-data outcomes instead of absolute counts. No production code was touched.
2. **Forward-fill join** — `app/api/crossover.py:96` changed from inner join to outer join, so a scan match whose price was forward-filled (not present exactly on `as_of`) now survives hydration with `latest_close: null` instead of being silently dropped. New regression test added.
3. **Cache sizing** — `crossover_loader.py:77` `lru_cache(maxsize=32)` → `maxsize=4`.

Full suite: **175 passed** (was 168 passed / 6 failed, +1 new regression test).

An independent scoped re-review (Opus) verified all three fixes in the live source, not just the diff — hand-traced the seed-data arithmetic for the rewritten tests and the new regression test, confirmed `latest_close` was already nullable in the schema, confirmed no scope creep. **Verdict: Ready to merge: Yes.**

One additional non-blocking Minor surfaced by the re-review, parked alongside the 7 below:

**11.** Rewritten tests now hard-depend on the dev DB having enough real calendar rows — `zip()` without `strict=True` in `tests/test_api.py` could silently under-seed on an unusually short calendar. Low-probability given this DB has 2+ years of data. Fix: add `strict=True` to the zip calls next time that file is touched.

Minor findings 4–11 (8 total) remain parked as non-blocking follow-up — not fixed in this branch, listed above for future reference.
