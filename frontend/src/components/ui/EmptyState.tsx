import type { CSSProperties, ReactNode } from 'react'
import { BlueprintButton } from './BlueprintButton'
import { BlueprintCard } from './BlueprintCard'

/** The icon + kicker + title + body + optional CTA "nothing here yet" card,
 * copy-pasted (with minor variations) in WatchlistsPage and AlertsPage
 * before this. */
export function EmptyState({
  icon,
  kicker,
  title,
  body,
  actionLabel,
  onAction,
  style,
}: {
  icon?: ReactNode
  kicker?: string
  title: string
  body: ReactNode
  actionLabel?: string
  onAction?: () => void
  style?: CSSProperties
}) {
  return (
    <BlueprintCard style={{ padding: 28, ...style }}>
      {icon}
      {kicker && <div className="card-kicker">{kicker}</div>}
      <div className="card-title">{title}</div>
      <p className="card-body">{body}</p>
      {actionLabel && onAction && (
        <BlueprintButton onClick={onAction} style={{ whiteSpace: 'nowrap' }}>
          {actionLabel}
        </BlueprintButton>
      )}
    </BlueprintCard>
  )
}
