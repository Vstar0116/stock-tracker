"""The curated "sunrise sector" grouping BS-GARP screens filter on.

Deliberately distinct from `instruments.industry` (NSE's own broad industry
taxonomy, populated market-wide by app/jobs/ingest_sector_classification.py):
these 9 labels are a narrower, hand-picked grouping specific to the BS-GARP
sunrise-sector universe, stored in `instruments.sector`. Defined once here and
imported everywhere a BS-GARP screen or the fundamentals seed job needs the
list, so the two never drift out of exact-string sync -- `sector` is matched
with a plain SQL `IN`, no fuzzy matching, so a retyped second copy is how a
screen silently returns zero matches.
"""

SUNRISE_SECTORS: tuple[str, ...] = (
    "Renewable Energy",
    "Defense & Aerospace",
    "EV & Battery",
    "Semiconductors & Electronics",
    "Green Hydrogen & Specialty Chemicals",
    "Water Treatment & Infrastructure",
    "Power Transmission Equipment",
    "Capital Goods",
    "Digital Infrastructure & Railways",
)
