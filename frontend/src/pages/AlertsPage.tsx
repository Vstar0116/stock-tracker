import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Corners } from '../components/Blueprint'
import { apiFetch } from '../lib/api'
import { ErrorText } from '../lib/format'
import { IconBell } from '../lib/icons'
import { usePageHeader } from '../lib/pageHeader'
import { useToast } from '../lib/toast'
import { useFetch } from '../lib/useFetch'
import type { AlertOut, Page, ScreenOut } from '../lib/types'

const DATE_RANGES = [
  { value: 'all', label: 'All time' },
  { value: 'today', label: 'Today' },
  { value: '7d', label: 'Last 7 days' },
  { value: '14d', label: 'Last 14 days' },
  { value: 'older', label: 'Older than 14 days' },
]

function daysAgo(dateStr: string): number {
  const d = new Date(dateStr + 'T00:00:00Z')
  const today = new Date()
  const todayUtc = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()))
  return Math.round((todayUtc.getTime() - d.getTime()) / 86400000)
}

function fmtDateLabel(iso: string): string {
  const d = new Date(iso + 'T00:00:00Z')
  return d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
}

function snapshotLine(snapshot: Record<string, number | string | null>): string {
  return Object.entries(snapshot)
    .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(2) : v}`)
    .join(', ')
}

export function AlertsPage() {
  usePageHeader('Alerts')
  const navigate = useNavigate()
  const { data, error: alertsError, reload } = useFetch<Page<AlertOut>>('/api/alerts?limit=200')
  const { data: screensPage } = useFetch<Page<ScreenOut>>('/api/screens?limit=200')

  const toast = useToast()
  const [screenFilter, setScreenFilter] = useState('all')
  const [dateRange, setDateRange] = useState('all')
  const [unseenOnly, setUnseenOnly] = useState(false)
  const [marking, setMarking] = useState(false)

  const alerts = data?.items ?? []
  const filtered = useMemo(() => {
    return alerts.filter((a) => {
      if (screenFilter !== 'all' && String(a.screen_id) !== screenFilter) return false
      if (unseenOnly && a.seen) return false
      if (dateRange !== 'all') {
        const days = daysAgo(a.trade_date)
        if (dateRange === 'today' && days !== 0) return false
        if (dateRange === '7d' && days > 7) return false
        if (dateRange === '14d' && days > 14) return false
        if (dateRange === 'older' && days <= 14) return false
      }
      return true
    })
  }, [alerts, screenFilter, dateRange, unseenOnly])

  const groups = useMemo(() => {
    const out: { date: string; label: string; items: AlertOut[] }[] = []
    let cur: (typeof out)[number] | null = null
    for (const a of filtered) {
      if (!cur || cur.date !== a.trade_date) {
        cur = { date: a.trade_date, label: fmtDateLabel(a.trade_date), items: [] }
        out.push(cur)
      }
      cur.items.push(a)
    }
    return out
  }, [filtered])

  async function markSeen(id: number) {
    try {
      await apiFetch(`/api/alerts/${id}/seen`, { method: 'POST' })
      reload()
    } catch {
      toast('Could not mark that alert as seen')
    }
  }

  async function markAllSeenFiltered() {
    const unseen = filtered.filter((a) => !a.seen)
    if (unseen.length === 0 || marking) return
    setMarking(true)
    try {
      const { updated } = await apiFetch<{ updated: number }>('/api/alerts/seen', {
        method: 'POST',
        body: JSON.stringify({ ids: unseen.map((a) => a.id) }),
      })
      toast(updated === unseen.length ? `Marked ${updated} as seen` : `Marked ${updated} of ${unseen.length}`)
    } catch {
      toast('Could not mark alerts as seen')
    } finally {
      setMarking(false)
      reload()
    }
  }

  return (
    <div style={{ maxWidth: 820 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap', marginBottom: 20 }}>
        <label className="field" style={{ margin: 0, width: 190 }}>
          <span className="field-label">Screen</span>
          <select className="input" value={screenFilter} onChange={(e) => setScreenFilter(e.target.value)}>
            <option value="all">All screens</option>
            {screensPage?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label className="field" style={{ margin: 0, width: 170 }}>
          <span className="field-label">Date range</span>
          <select className="input" value={dateRange} onChange={(e) => setDateRange(e.target.value)}>
            {DATE_RANGES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, paddingBottom: 9, cursor: 'pointer' }}>
          <input type="checkbox" checked={unseenOnly} onChange={(e) => setUnseenOnly(e.target.checked)} />
          Unseen only
        </label>
        <div style={{ flex: 1 }} />
        <button
          type="button" className="btn btn-secondary" onClick={markAllSeenFiltered} disabled={marking}
          style={{ marginBottom: 0, whiteSpace: 'nowrap', flexShrink: 0 }}
        >
          {marking ? 'Marking…' : 'Mark all as seen'}
        </button>
      </div>

      {alertsError && <ErrorText>Couldn't load alerts: {alertsError}</ErrorText>}

      {!alertsError && filtered.length === 0 && (
        <div className="card blueprint" style={{ maxWidth: 520, padding: 30 }}>
          <Corners />
          <IconBell size={34} strokeWidth={1.3} stroke="var(--color-neutral-500)" style={{ marginBottom: 12 }} />
          <div className="card-title">No alerts match these filters</div>
          <p className="card-body">
            Alerts fire here automatically whenever a saved screen finds a new match. Try widening the filters above, or set up a new screen to start watching for something.
          </p>
          <button type="button" className="btn btn-primary blueprint" onClick={() => navigate('/screener')} style={{ whiteSpace: 'nowrap', alignSelf: 'flex-start' }}>
            <Corners />
            Go to Screener
          </button>
        </div>
      )}

      {groups.map((grp) => (
        <div key={grp.date} style={{ marginBottom: 22 }}>
          <h2 className="section-label">{grp.label}</h2>
          {grp.items.map((a) => (
            <div
              key={a.id}
              style={{
                display: 'flex', gap: 12, alignItems: 'flex-start', padding: '12px 14px', marginBottom: 6,
                border: '1px solid var(--color-neutral-300)', background: a.seen ? 'transparent' : 'var(--color-accent-100)',
              }}
            >
              <div style={{ width: 8, height: 8, marginTop: 6, flex: 'none', background: a.seen ? 'var(--color-neutral-300)' : 'var(--color-accent-600)' }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 3 }}>
                  <span className="tag tag-accent" style={{ whiteSpace: 'nowrap' }}>{a.screen_name}</span>
                  <Link to={`/stocks/${a.instrument_id}`} state={{ from: '/alerts', fromLabel: 'Alerts' }}>
                    <strong>{a.symbol}</strong>
                  </Link>
                  <span style={{ color: 'var(--color-neutral-600)', fontSize: 12.5, whiteSpace: 'nowrap' }}>{a.exchange}</span>
                </div>
                <div style={{ fontSize: 13.5, marginBottom: 3 }}>matched on {a.trade_date}</div>
                <div style={{ fontSize: 12, color: 'var(--color-neutral-600)', fontVariantNumeric: 'tabular-nums' }}>{snapshotLine(a.snapshot)}</div>
              </div>
              {a.seen ? (
                <span style={{ fontSize: 11.5, color: 'var(--color-neutral-500)', whiteSpace: 'nowrap', paddingTop: 2 }}>Seen</span>
              ) : (
                <button type="button" className="btn btn-secondary" onClick={() => markSeen(a.id)} style={{ whiteSpace: 'nowrap', flexShrink: 0, fontSize: 12, padding: '5px 11px', marginBottom: 0 }}>
                  Mark seen
                </button>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
