import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { ErrorBoundary } from './ErrorBoundary'
import { apiFetch } from '../lib/api'
import { useAuth } from '../lib/auth'
import { IconActivity, IconBell, IconCheckCircle, IconList, IconMenu, IconSearch, IconSliders, IconWarningTriangle } from '../lib/icons'
import { useHeader } from '../lib/pageHeader'
import type { AlertOut, Page, StatusOut } from '../lib/types'

const POLL_MS = 5 * 60_000

const NAV_ITEMS = [
  { to: '/watchlists', label: 'Watchlists', icon: <IconList /> },
  { to: '/screener', label: 'Screener', icon: <IconSliders /> },
  { to: '/scan', label: 'Custom Scan', icon: <IconSearch /> },
  { to: '/alerts', label: 'Alerts', icon: <IconBell /> },
  { to: '/status', label: 'Status', icon: <IconActivity /> },
]

function FreshnessBox() {
  const [status, setStatus] = useState<StatusOut | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => apiFetch<StatusOut>('/api/status').then((s) => !cancelled && setStatus(s)).catch(() => {})
    load()
    const id = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  if (!status) return null
  const isStale = !status.is_current
  const border = isStale ? 'var(--color-warn-border)' : 'var(--color-pos-border)'
  const bg = isStale ? 'var(--color-warn-bg)' : 'var(--color-pos-bg)'
  const text = isStale ? 'var(--color-warn-text)' : 'var(--color-pos-text)'
  const label = status.latest_trade_date ? `Data as of ${status.latest_trade_date}` : 'No data yet'
  const sub = isStale
    ? `Expected ${status.expected_trade_date} — tonight's pipeline may not have completed`
    : 'Pipeline completed on schedule'

  return (
    <div
      className="freshness-box"
      style={{
        display: 'flex', alignItems: 'center', gap: 9, padding: '7px 13px', fontFamily: 'var(--font-body)',
        flexShrink: 0, whiteSpace: 'nowrap', border: `1px solid ${border}`, background: bg, color: text,
      }}
      title={`last pipeline run: ${status.last_pipeline_run_at ?? 'never'} (${status.last_pipeline_status ?? 'unknown'})`}
    >
      {isStale ? <IconWarningTriangle /> : <IconCheckCircle />}
      <div>
        <div style={{ fontWeight: 600, fontSize: 12.5, whiteSpace: 'nowrap' }}>{label}</div>
        <div className="freshness-sub" style={{ fontSize: 11, opacity: 0.85, whiteSpace: 'nowrap' }}>{sub}</div>
      </div>
    </div>
  )
}

function useUnseenAlertsCount(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let cancelled = false
    const load = () =>
      apiFetch<Page<AlertOut>>('/api/alerts?seen=false&limit=1')
        .then((p) => !cancelled && setCount(p.total))
        .catch(() => {})
    load()
    const id = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])
  return count
}

export function AppShell() {
  const { user, logout } = useAuth()
  const header = useHeader()
  const unseenAlerts = useUnseenAlertsCount()
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()

  return (
    <div className={navOpen ? 'app-shell nav-open' : 'app-shell'}>
      <div className="app-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <aside className="app-sidebar">
        <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.01em', padding: '0 8px 4px' }}>NSE TRACKER</div>
        <div style={{ fontSize: 11, color: 'var(--color-neutral-600)', padding: '0 8px 22px', letterSpacing: '0.02em', whiteSpace: 'nowrap' }}>
          Watchlists · Screener · Alerts
        </div>

        <nav aria-label="Main">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              // Below 900px the sidebar is an overlay, so navigating has to
              // close it -- otherwise it covers the page just asked for.
              onClick={() => setNavOpen(false)}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left', border: 'none',
                background: isActive ? 'var(--color-accent-100)' : 'none', cursor: 'pointer', padding: '9px 10px', marginBottom: 2,
                fontFamily: 'var(--font-body)', fontSize: 14, fontWeight: 500, whiteSpace: 'nowrap', textDecoration: 'none',
                transition: 'background var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out)',
                color: isActive ? 'var(--color-accent-900)' : 'var(--color-neutral-700)',
              })}
            >
              {item.icon}
              {item.label}
              {item.to === '/alerts' && unseenAlerts > 0 && (
                <span style={{ marginLeft: 'auto', background: 'var(--color-brand)', color: '#fff', fontSize: 11, fontWeight: 600, padding: '1px 7px', minWidth: 18, textAlign: 'center' }}>
                  {unseenAlerts}
                  <span className="sr-only"> unseen</span>
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div style={{ flex: 1 }} />
        <div style={{ padding: 8, fontSize: 11, color: 'var(--color-neutral-700)', lineHeight: 1.5 }}>
          Internal tracking tool — for informational purposes only.
          <br />
          Not investment advice. No trading or order placement.
        </div>
      </aside>

      <div className="app-body">
        <header className="app-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
            <button
              type="button"
              className="btn btn-secondary app-nav-toggle"
              onClick={() => setNavOpen((o) => !o)}
              aria-label="Menu"
              aria-expanded={navOpen}
            >
              <IconMenu />
            </button>
            <div style={{ minWidth: 0 }}>
              <h1 style={{ margin: 0, fontSize: 20 }}>{header.title}</h1>
              {header.subtitle && <div className="page-subtitle" style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginTop: 2 }}>{header.subtitle}</div>}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <FreshnessBox />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5, color: 'var(--color-neutral-600)' }}>
              <span className="user-name">{user?.name}</span>
              <button type="button" onClick={logout} className="btn btn-ghost" style={{ fontSize: 12.5, padding: '2px 6px' }}>
                Log out
              </button>
            </div>
          </div>
        </header>
        <main className="app-main">
          {/* Keyed on the route: a crash on one page shouldn't leave the error
              card stuck in place after the user navigates away. */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
