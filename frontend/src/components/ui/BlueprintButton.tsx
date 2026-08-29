import type { ButtonHTMLAttributes } from 'react'
import { BlueprintCorners } from './BlueprintCorners'

/** The primary-CTA `.btn .btn-primary .blueprint` button, corners included.
 * `type` defaults to "button" (nearly every existing use) but stays
 * overridable for the one `type="submit"` case (WatchlistsPage's inline
 * "Add" form). */
export function BlueprintButton({
  className = '',
  type = 'button',
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type={type} className={`btn btn-primary blueprint ${className}`.trim()} {...rest}>
      <BlueprintCorners />
      {children}
    </button>
  )
}
