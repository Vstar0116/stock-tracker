import type { ReactNode } from 'react'

/** Bordered "nothing here" box. Takes a hint as well as a title because an
 *  empty state that only says "no matches" tells the user nothing about what
 *  to do next -- the same reason the Watchlists and Alerts empty states
 *  already explain themselves. */
export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div style={{ padding: 26, textAlign: 'center', border: '1px solid var(--color-neutral-300)' }}>
      <div style={{ fontSize: 13, color: 'var(--color-neutral-700)' }}>{title}</div>
      {hint && (
        <div style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginTop: 5, maxWidth: 460, marginInline: 'auto' }}>
          {hint}
        </div>
      )}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  )
}
