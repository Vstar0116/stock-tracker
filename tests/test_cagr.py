"""Tests for app/services/cagr.py's pure calculation -- no database needed.

instrument_cagrs() itself (the DB-querying wrapper) is exercised indirectly
via the /instruments/{id} endpoint; this file only pins the math and the
None-on-too-little-history edge case.

Run with: pytest tests/test_cagr.py -v
"""

import pytest

from app.services.cagr import _pure_cagr


class TestPureCagr:
    def test_doubling_over_one_year(self):
        # (200/100)^(1/1) - 1 = 1.0 (100% growth in a year)
        assert _pure_cagr(200, 100, 1.0) == pytest.approx(1.0)

    def test_known_multi_year_growth(self):
        # (1000/500)^(1/5) - 1 -- hand-computed: 2^0.2 - 1 ~= 0.148698
        assert _pure_cagr(1000, 500, 5.0) == pytest.approx(0.148698, abs=1e-5)

    def test_flat_price_is_zero_cagr(self):
        assert _pure_cagr(100, 100, 3.0) == pytest.approx(0.0)

    def test_decline_is_negative(self):
        # (50/100)^(1/2) - 1 -- hand-computed: 0.5^0.5 - 1 ~= -0.29289
        assert _pure_cagr(50, 100, 2.0) == pytest.approx(-0.29289, abs=1e-5)

    def test_non_positive_start_price_is_none(self):
        assert _pure_cagr(100, 0, 1.0) is None

    def test_zero_elapsed_years_is_none(self):
        # Guards div-by-zero -- can't annualize over no elapsed time.
        assert _pure_cagr(100, 100, 0.0) is None
