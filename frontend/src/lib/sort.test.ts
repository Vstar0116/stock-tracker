import { describe, expect, it } from 'vitest'
import { compareRows } from './sort'

const sorted = (values: unknown[], dir: 'asc' | 'desc') =>
  [...values].sort((a, b) => compareRows(a, b, dir))

describe('compareRows', () => {
  it('orders numbers numerically, not lexically', () => {
    expect(sorted([100, 9, 1835], 'asc')).toEqual([9, 100, 1835])
    expect(sorted([100, 9, 1835], 'desc')).toEqual([1835, 100, 9])
  })

  it('orders strings alphabetically in both directions', () => {
    expect(sorted(['TCS', 'INFY', 'ABB'], 'asc')).toEqual(['ABB', 'INFY', 'TCS'])
    expect(sorted(['TCS', 'INFY', 'ABB'], 'desc')).toEqual(['TCS', 'INFY', 'ABB'])
  })

  // The whole reason this helper exists: a column of missing indicators must
  // not ride to the top when the user flips to descending.
  it('keeps nulls last in both directions', () => {
    expect(sorted([3, null, 1], 'asc')).toEqual([1, 3, null])
    expect(sorted([3, null, 1], 'desc')).toEqual([3, 1, null])
  })

  it.each([null, undefined, NaN, ''])('treats %p as missing', (missing) => {
    expect(sorted([missing, 5], 'asc')[1]).toBe(missing)
    expect(sorted([missing, 5], 'desc')[1]).toBe(missing)
  })

  it('handles negative numbers as values, not as missing', () => {
    expect(sorted([-2.5, 0, -10], 'asc')).toEqual([-10, -2.5, 0])
  })
})
