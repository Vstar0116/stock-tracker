import type { HTMLAttributes } from 'react'
import { BlueprintCorners } from './BlueprintCorners'

/** The `.card .blueprint` info/empty-state card, corners included. */
export function BlueprintCard({ className = '', children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`card blueprint ${className}`.trim()} {...rest}>
      <BlueprintCorners />
      {children}
    </div>
  )
}
