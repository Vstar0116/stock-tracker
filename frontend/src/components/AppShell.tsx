import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useHeader } from '../lib/pageHeader'
import type { AlertOut, Page, StatusOut } from '../lib/types'

const POLL_MS = 5 * 60_000

const NAV_ITEMS = [
  {
    to: '/watchlists',
    label: 'Watchlists',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" />
        <line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
      </svg>
    ),
  },
  {
    to: '/screener',
    label: 'Screener',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" />
        <line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" />
      </svg>
    ),
  },
  {
    to: '/alerts',
    label: 'Alerts',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
      </svg>
    ),
  },
  {
    to: '/status',
    label: 'Status',
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
      </svg>
    ),
  },
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
      style={{
        display: 'flex', alignItems: 'center', gap: 9, padding: '7px 13px', fontFamily: 'var(--font-body)',
        flexShrink: 0, whiteSpace: 'nowrap', border: `1px solid ${border}`, background: bg, color: text,
      }}
      title={`last pipeline run: ${status.last_pipeline_run_at ?? 'never'} (${status.last_pipeline_status ?? 'unknown'})`}
    >
      {isStale ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" />
        </svg>
      )}
      <div>
        <div style={{ fontWeight: 600, fontSize: 12.5, whiteSpace: 'nowrap' }}>{label}</div>
        <div style={{ fontSize: 11, opacity: 0.85, whiteSpace: 'nowrap' }}>{sub}</div>
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

  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--color-bg)', color: 'var(--color-text)', fontFamily: 'var(--font-body)', fontSize: 14 }}>
      <aside style={{ width: 220, flex: 'none', borderRight: '1px solid var(--color-divider)', display: 'flex', flexDirection: 'column', padding: '20px 14px' }}>
        <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.01em', padding: '0 8px 4px' }}>NSE TRACKER</div>
        <div style={{ fontSize: 11, color: 'var(--color-neutral-600)', padding: '0 8px 22px', letterSpacing: '0.02em', whiteSpace: 'nowrap' }}>
          Watchlists · Screener · Alerts
        </div>

        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            style={({ isActive }) => ({
              display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left', border: 'none',
              background: isActive ? 'var(--color-accent-100)' : 'none', cursor: 'pointer', padding: '9px 10px', marginBottom: 2,
              fontFamily: 'var(--font-body)', fontSize: 14, fontWeight: 500, whiteSpace: 'nowrap', textDecoration: 'none',
              color: isActive ? 'var(--color-accent-900)' : 'var(--color-neutral-700)',
            })}
          >
            {item.icon}
            {item.label}
            {item.to === '/alerts' && unseenAlerts > 0 && (
              <span style={{ marginLeft: 'auto', background: 'var(--color-accent-600)', color: '#fff', fontSize: 11, fontWeight: 600, padding: '1px 7px', minWidth: 18, textAlign: 'center' }}>
                {unseenAlerts}
              </span>
            )}
          </NavLink>
        ))}

        <div style={{ flex: 1 }} />
        <div style={{ padding: 8, fontSize: 11, color: 'var(--color-neutral-500)', lineHeight: 1.5 }}>
          Internal tracking tool.
          <br />
          No trading or order placement.
        </div>
      </aside>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
        <header style={{ flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '16px 28px', borderBottom: '1px solid var(--color-divider)' }}>
          <div>
            <h4 style={{ margin: 0 }}>{header.title}</h4>
            {header.subtitle && <div style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginTop: 2 }}>{header.subtitle}</div>}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <FreshnessBox />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12.5, color: 'var(--color-neutral-600)' }}>
              <span>{user?.name}</span>
              <button type="button" onClick={logout} className="btn btn-ghost" style={{ fontSize: 12.5, padding: '2px 6px' }}>
                Log out
              </button>
            </div>
          </div>
        </header>
        <main style={{ flex: 1, overflowY: 'auto', padding: '24px 28px 60px' }}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
