"""Extracts BS-V4-style protocol thresholds from an uploaded PDF, so a user
can populate Zone Classifier parameters from a document instead of typing
each value in by hand.

Deterministic regex extraction only -- no LLM involved (CLAUDE.md principle
3: numeric answers come from code, never a model guessing). Only the
`ZoneParams` numeric thresholds are read out of the document; any buy/sell
-advice language a "protocol" PDF might contain (e.g. "Aggressive Dip Buy",
"Market Order") is never extracted or surfaced -- this app's zone output
stays the neutral technical-state labels described in
docs/superpowers/specs/2026-08-25-bs-v4-zone-classifier-design.md, and this
parser only ever produces the numbers that already back those existing
`ZoneParams` fields.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Mirrors app/services/zone_classifier.py::ZoneParams field names exactly,
# so a caller can merge `found` straight into the params dict/form state.
ZONE_PARAM_FIELDS = [
    "macro_sma_period",
    "fast_ema_period",
    "slow_ema_period",
    "rsi_period",
    "rsi_zone_a_max",
    "rsi_zone_b_low",
    "rsi_zone_b_high",
    "rsi_zone_c_low",
    "rsi_zone_c_high",
    "rsi_zone_d_min",
    "atr_period",
    "atr_limit_multiplier",
    "rvol_period",
    "near_ema_pct",
]


def extract_pdf_text(data: bytes, max_pages: int = 15) -> str:
    """Raises PdfReadError on a malformed/unreadable PDF -- callers should
    turn that into a 400, not a 500. Only the first `max_pages` are read,
    both because a protocol document is short and to bound the work done on
    an arbitrary uploaded file."""
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages[:max_pages]
    return "\n".join(page.extract_text() or "" for page in pages)


def parse_protocol_text(text: str) -> tuple[dict[str, float], list[str]]:
    """Returns (found, not_found). `found` only contains fields the text
    matched with high confidence -- a field simply absent from `found` is
    left for the caller to keep at its current/default value, never guessed."""
    # PDF text extraction from some document sources (Google Docs exports,
    # notably -- confirmed against the real BS-V4 protocol PDF) sprinkles in
    # zero-width spaces and soft hyphens around every bullet/heading; strip
    # those and collapse whitespace before any regex sees the text.
    normalized = re.sub(r"[​­]", "", text)  # zero-width space, soft hyphen
    normalized = re.sub(r"\s+", " ", normalized)

    found: dict[str, float] = {}

    m = re.search(r"(\d+)\s*-?\s*day\s+simple\s+moving\s+average", normalized, re.I)
    if m:
        found["macro_sma_period"] = float(m.group(1))

    ema_days = re.findall(r"(\d+)\s*-?\s*day\s+exponential\s+moving\s+average", normalized, re.I)
    if len(ema_days) >= 2:
        fast, slow = float(ema_days[0]), float(ema_days[1])
        found["fast_ema_period"], found["slow_ema_period"] = min(fast, slow), max(fast, slow)

    m = re.search(r"(\d+)\s*-?\s*period\s+rsi", normalized, re.I) or re.search(r"rsi\s*\(\s*(\d+)\s*\)", normalized, re.I)
    if m:
        found["rsi_period"] = float(m.group(1))

    m = re.search(r"rsi\s*<\s*(\d+(?:\.\d+)?)", normalized, re.I)
    if m:
        found["rsi_zone_a_max"] = float(m.group(1))

    ranges = re.findall(r"rsi\s+between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)", normalized, re.I)
    if len(ranges) >= 2:
        found["rsi_zone_b_low"], found["rsi_zone_b_high"] = float(ranges[0][0]), float(ranges[0][1])
        found["rsi_zone_c_low"], found["rsi_zone_c_high"] = float(ranges[1][0]), float(ranges[1][1])

    d_matches = re.findall(r"rsi\s*(?:>=|\\ge|≥|>)\s*(\d+(?:\.\d+)?)", normalized, re.I)
    if d_matches:
        found["rsi_zone_d_min"] = float(d_matches[0])

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\\times|[x×*])\s*atr", normalized, re.I)
    if m:
        found["atr_limit_multiplier"] = float(m.group(1))

    m = re.search(r"(\d+)\s*-?\s*period\s+atr", normalized, re.I)
    if m:
        found["atr_period"] = float(m.group(1))

    m = re.search(r"(\d+)\s*-?\s*period\s+volume\s+sma", normalized, re.I)
    if m:
        found["rvol_period"] = float(m.group(1))

    m = re.search(r"within\s+(\d+(?:\.\d+)?)\s*%\s*of\s*(?:an?\s*)?ema", normalized, re.I)
    if m:
        found["near_ema_pct"] = float(m.group(1)) / 100

    not_found = [f for f in ZONE_PARAM_FIELDS if f not in found]
    return found, not_found


__all__ = ["ZONE_PARAM_FIELDS", "PdfReadError", "extract_pdf_text", "parse_protocol_text"]
