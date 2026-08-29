/** Mirrors app/services/crossover.py::validate_periods -- the same rule the
 * backend enforces (fast a positive integer strictly less than slow, slow
 * capped at 400). Returns true when the params are INVALID (matches the
 * `invalid` boolean this replaces). Used to be a byte-identical inline
 * expression duplicated in CustomScanPage.tsx and StockDetailPage.tsx's
 * CustomCrossoverCard. */
export function isCrossoverInvalid(fast: string, slow: string): boolean {
  const fastNum = Number(fast)
  const slowNum = Number(slow)
  return !Number.isInteger(fastNum) || !Number.isInteger(slowNum) || fastNum < 1 || fastNum >= slowNum || slowNum > 400
}
