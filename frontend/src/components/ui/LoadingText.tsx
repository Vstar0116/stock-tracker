/** Consistent "Loading…" text -- used to be inconsistent per page (a bare
 * `<p>Loading…</p>`, sometimes nothing at all). Also App.tsx's route-level
 * Suspense fallback. */
export function LoadingText({ children = 'Loading…' }: { children?: string }) {
  return (
    <p className="text-muted" style={{ fontSize: 13, padding: 24, textAlign: 'center' }}>
      {children}
    </p>
  )
}
