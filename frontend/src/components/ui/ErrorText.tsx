import type { CSSProperties, ReactNode } from 'react'

/** The `color: var(--color-neg-text)` inline error paragraph, copy-pasted
 * 14 times before this with slightly different font sizes/margins each
 * time. */
export function ErrorText({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <p style={{ fontSize: 13, color: 'var(--color-neg-text)', ...style }}>{children}</p>
}
