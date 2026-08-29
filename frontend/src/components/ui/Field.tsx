import type { CSSProperties, ReactNode } from 'react'

/** The label + input/select `.field` wrapper, copy-pasted 13 times before
 * this. Callers keep full control of the actual control via `children` --
 * this only standardizes the wrapper and label. `hideLabel` keeps the
 * label in the DOM (for alignment with sibling fields in a row) but hides
 * it visually, the same trick CustomScanPage's watchlist-only checkbox
 * used inline. */
export function Field({
  label,
  children,
  style,
  hideLabel = false,
}: {
  label: string
  children: ReactNode
  style?: CSSProperties
  hideLabel?: boolean
}) {
  return (
    <div className="field" style={{ margin: 0, ...style }}>
      <label style={hideLabel ? { visibility: 'hidden' } : undefined}>{label}</label>
      {children}
    </div>
  )
}
