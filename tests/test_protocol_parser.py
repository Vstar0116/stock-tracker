from app.services.protocol_parser import ZONE_PARAM_FIELDS, parse_protocol_text

# Mirrors the real BS-V4 protocol PDF's phrasing (extracted via pdftotext),
# including the zero-width spaces/soft hyphens that document's export adds
# around nearly every bullet and heading.
BS_V4_TEXT = """
2.1 The Macro Regime Filter (Trend Baseline)
   ​ ​I​ndicator:​​200-Day Simple Moving Average (200 SMA).​

2.2 The Tactical Engine (Entry/Exit Triggers)
   ​●​ I​ndicators:​​9-Day Exponential Moving Average (9 EMA)​​and 21-Day Exponential​
           ​Moving Average (21 EMA).​

Zone A: RSI < 55
Zone B: RSI between 56 and 65
Zone C: RSI between 66 and 71
Zone D: RSI > 72

No fresh allocations are permitted if the 14-period RSI exceeds 65.
The moment an asset reaches an $RSI \\ge 72$, it is automatically reclassified.

21 EMA + (0.25 \\times ATR)

$$RVOL = \\frac{\\text{Current Volume}}{\\text{20-Period Volume SMA}}$$
"""


def test_extracts_moving_average_periods():
    found, _ = parse_protocol_text(BS_V4_TEXT)
    assert found["macro_sma_period"] == 200
    assert found["fast_ema_period"] == 9
    assert found["slow_ema_period"] == 21


def test_extracts_ema_periods_in_ascending_order_regardless_of_document_order():
    found, _ = parse_protocol_text("uses the 21-Day Exponential Moving Average and the 9-Day Exponential Moving Average")
    assert found["fast_ema_period"] == 9
    assert found["slow_ema_period"] == 21


def test_extracts_rsi_period():
    found, _ = parse_protocol_text(BS_V4_TEXT)
    assert found["rsi_period"] == 14


def test_extracts_rsi_zone_thresholds():
    found, _ = parse_protocol_text(BS_V4_TEXT)
    assert found["rsi_zone_a_max"] == 55
    assert found["rsi_zone_b_low"] == 56
    assert found["rsi_zone_b_high"] == 65
    assert found["rsi_zone_c_low"] == 66
    assert found["rsi_zone_c_high"] == 71
    assert found["rsi_zone_d_min"] == 72


def test_extracts_atr_limit_multiplier():
    found, _ = parse_protocol_text(BS_V4_TEXT)
    assert found["atr_limit_multiplier"] == 0.25


def test_extracts_rvol_period():
    found, _ = parse_protocol_text(BS_V4_TEXT)
    assert found["rvol_period"] == 20


def test_fields_absent_from_document_are_reported_not_found_not_guessed():
    found, not_found = parse_protocol_text(BS_V4_TEXT)
    # This document never states an explicit ATR period or a near-EMA
    # percentage, so both must be left for the caller to decide, not guessed.
    assert "atr_period" not in found
    assert "near_ema_pct" not in found
    assert "atr_period" in not_found
    assert "near_ema_pct" in not_found


def test_found_and_not_found_partition_all_fields():
    found, not_found = parse_protocol_text(BS_V4_TEXT)
    assert set(found) | set(not_found) == set(ZONE_PARAM_FIELDS)
    assert set(found) & set(not_found) == set()


def test_empty_document_finds_nothing():
    found, not_found = parse_protocol_text("")
    assert found == {}
    assert not_found == ZONE_PARAM_FIELDS


def test_zero_width_space_between_letters_does_not_block_a_match():
    text = "The​ ​20-Period​ Volume​ SMA​ measures​ participation​."
    found, _ = parse_protocol_text(text)
    assert found["rvol_period"] == 20


def test_near_ema_percentage_is_converted_to_a_fraction():
    found, _ = parse_protocol_text("Price consolidating within 2% of an EMA confirms accumulation.")
    assert found["near_ema_pct"] == 0.02


def test_single_rsi_between_range_is_not_assigned_to_zone_b_or_c():
    # Only one "RSI between X and Y" match is ambiguous (could be either
    # zone) -- must not guess which.
    found, not_found = parse_protocol_text("Only one range here: RSI between 56 and 65.")
    assert "rsi_zone_b_low" not in found
    assert "rsi_zone_c_low" not in found
    assert "rsi_zone_b_low" in not_found
