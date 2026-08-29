import type { TrendState } from './types'

export function indianNum(num: number | null | undefined, decimals = 2): string {
  if (num === null || num === undefined || isNaN(num)) return '—'
  const sign = num < 0 ? '-' : ''
  num = Math.abs(num)
  const fixed = num.toFixed(decimals)
  // toFixed's own output always has a non-empty part before any '.', so
  // this fallback never actually fires -- it's here only to satisfy
  // noUncheckedIndexedAccess's (correct, in general) "an array index could
  // be out of bounds" typing.
  const [intPart = '0', decPart] = fixed.split('.')
  const last3 = intPart.slice(-3)
  let rest = intPart.slice(0, -3)
  if (rest !== '') rest = rest.replace(/\B(?=(\d{2})+(?!\d)$)/g, ',') + ','
  return sign + rest + last3 + (decPart !== undefined ? '.' + decPart : '')
}

export function fmtPrice(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : '₹' + indianNum(n, 2)
}

export function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined || isNaN(n)) return '—'
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}

export function fmtNum(n: number | null | undefined, decimals = 2): string {
  return indianNum(n, decimals)
}

export interface ChangeVisual {
  up: boolean
  down: boolean
  flat: boolean
  color: string
}

export function changeVisual(n: number | null | undefined): ChangeVisual {
  const v = n ?? 0
  const up = v > 0.0001
  const down = v < -0.0001
  return { up, down, flat: !up && !down, color: up ? 'var(--color-pos-text)' : down ? 'var(--color-neg-text)' : 'var(--color-neutral-600)' }
}

export interface TrendVisual {
  up: boolean
  down: boolean
  flat: boolean
  label: string
  color: string
  bg: string
  border: string
}

export function trendVisual(trend: TrendState): TrendVisual {
  if (trend === 'uptrend') {
    return { up: true, down: false, flat: false, label: 'Uptrend', color: 'var(--color-pos-text)', bg: 'var(--color-pos-bg)', border: 'var(--color-pos-border)' }
  }
  if (trend === 'downtrend') {
    return { up: false, down: true, flat: false, label: 'Downtrend', color: 'var(--color-neg-text)', bg: 'var(--color-neg-bg)', border: 'var(--color-neg-border)' }
  }
  const label = trend === 'neutral' ? 'Sideways' : 'Unknown'
  return { up: false, down: false, flat: true, label, color: 'var(--color-neutral-700)', bg: 'var(--color-neutral-200)', border: 'var(--color-neutral-400)' }
}

/** Small up/down/flat triangle-or-dash icon matching the design's inline SVGs. */
export function ChangeGlyph({ v }: { v: ChangeVisual }) {
  if (v.up) {
    return (
      <svg width="9" height="9" viewBox="0 0 10 10" fill="currentColor">
        <polygon points="5,1 9,8 1,8" />
      </svg>
    )
  }
  if (v.down) {
    return (
      <svg width="9" height="9" viewBox="0 0 10 10" fill="currentColor">
        <polygon points="1,2 9,2 5,9" />
      </svg>
    )
  }
  return (
    <svg width="9" height="9" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <line x1="1.5" y1="5" x2="8.5" y2="5" />
    </svg>
  )
}
