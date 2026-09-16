import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { CommandPalette } from './CommandPalette'
import { ErrorBoundary } from './ErrorBoundary'
import { LiquidMetalMark } from './LiquidMetalMark'
import { apiFetch } from '../lib/api'
import { useAuth } from '../lib/auth'
import { IconActivity, IconBell, IconBriefcase, IconCheckCircle, IconHome, IconList, IconLogout, IconMenu, IconSearch, IconSliders, IconWand, IconWarningTriangle } from '../lib/icons'
import { useHeader } from '../lib/pageHeader'
import type { AlertOut, Page, StatusOut } from '../lib/types'

const POLL_MS = 5 * 60_000

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: <IconHome /> },
  { to: '/watchlists', label: 'Watchlists', icon: <IconList /> },
  { to: '/portfolio', label: 'Portfolio', icon: <IconBriefcase /> },
  { to: '/screener', label: 'Screener', icon: <IconSliders /> },
  { to: '/alert-wizard', label: 'Alert Wizard', icon: <IconWand /> },
  { to: '/scan', label: 'Custom Scan', icon: <IconSearch /> },
  { to: '/alerts', label: 'Alerts', icon: <IconBell /> },
  { to: '/status', label: 'Status', icon: <IconActivity /> },
]

function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

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
  const dotColor = isStale ? 'var(--color-warn-text)' : 'var(--color-pos-text)'
  const label = isStale ? 'Data one day behind' : 'Data current'
  const sub = isStale
    ? `Expected ${status.expected_trade_date}, showing ${status.latest_trade_date ?? 'no data'}`
    : status.latest_trade_date
      ? `Close of ${status.latest_trade_date} ingested`
      : 'Pipeline completed on schedule'

  return (
    <div
      className="freshness-box"
      style={{ background: 'var(--color-surface-2)', borderRadius: 18, padding: 16, fontFamily: 'var(--font-body)' }}
      title={`last pipeline run: ${status.last_pipeline_run_at ?? 'never'} (${status.last_pipeline_status ?? 'unknown'})`}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, color: dotColor }}>
        {isStale ? <IconWarningTriangle size={14} /> : <IconCheckCircle size={14} />}
        <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
      </div>
      <div className="freshness-sub" style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', lineHeight: 1.45 }}>{sub}</div>
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
  const [paletteOpen, setPaletteOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <div className={navOpen ? 'app-shell nav-open' : 'app-shell'}>
      <div className="app-scrim" onClick={() => setNavOpen(false)} aria-hidden="true" />

      <aside className="app-sidebar">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px 22px' }}>
          <LiquidMetalMark size={32} />
          <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: 18, letterSpacing: '-0.02em' }}>NSE Tracker</span>
        </div>

        <button type="button" className="palette-trigger" onClick={() => setPaletteOpen(true)}>
          <IconSearch size={15} />
          <span style={{ flex: 1 }}>Search</span>
          <kbd>⌘K</kbd>
        </button>

        <nav aria-label="Main">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              // Below 900px the sidebar is an overlay, so navigating has to
              // close it -- otherwise it covers the page just asked for.
              onClick={() => setNavOpen(false)}
              className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
            >
              {item.icon}
              {item.label}
              {item.to === '/alerts' && unseenAlerts > 0 && (
                <span className="nav-badge">
                  {unseenAlerts}
                  <span className="sr-only"> unseen</span>
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        <div style={{ flex: 1 }} />

        <FreshnessBox />

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 4px 2px' }}>
          <div style={{ width: 32, height: 32, borderRadius: 999, background: 'var(--color-surface-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 600, color: 'var(--color-accent-800)', flex: 'none' }}>
            {initials(user?.name ?? '')}
          </div>
          <div className="user-name" style={{ flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {user?.name}
          </div>
          <button type="button" onClick={logout} className="btn btn-ghost btn-icon" aria-label="Log out">
            <IconLogout size={15} />
          </button>
        </div>
        <div style={{ padding: '8px 4px 0', fontSize: 11, color: 'var(--color-neutral-600)', lineHeight: 1.5 }}>
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
        </header>
        <main className="app-main">
          {/* Keyed on the route: a crash on one page shouldn't leave the error
              card stuck in place after the user navigates away. */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} pages={NAV_ITEMS.map(({ to, label }) => ({ to, label }))} />
    </div>
  )
}
