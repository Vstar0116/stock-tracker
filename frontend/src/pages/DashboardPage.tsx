import { Link, Navigate } from 'react-router-dom'
import { changeVisual, ChangeGlyph, ErrorText, fmtPct, fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { useFetch } from '../lib/useFetch'
import type { DashboardMoverOut, DashboardOut, SectorHeatOut } from '../lib/types'

function BreadthStat({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 9 }}>
      <span style={{ fontFamily: 'var(--font-heading)', fontSize: 22, fontWeight: 700, color: color ?? 'var(--color-text)' }}>{value}</span>
      <span style={{ fontSize: 14, color: 'var(--color-neutral-600)' }}>{label}</span>
    </div>
  )
}

function MoverRow({ m }: { m: DashboardMoverOut }) {
  const v = changeVisual(m.change_pct)
  return (
    <Link
      to={`/stocks/${m.instrument_id}`}
      style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '12px 0', color: 'inherit', textDecoration: 'none', borderTop: '1px solid var(--color-divider)' }}
    >
      <div style={{ width: 120, minWidth: 0 }}>
        <div style={{ fontSize: 14.5, fontWeight: 600 }}>{m.symbol}</div>
        <div style={{ fontSize: 12, color: 'var(--color-neutral-600)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.sector ?? m.exchange}</div>
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        <div style={{ fontSize: 14.5, fontWeight: 600 }}>{fmtPrice(m.close)}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end', fontSize: 13, fontWeight: 600, color: v.color }}>
          <ChangeGlyph v={v} />
          {fmtPct(m.change_pct)}
        </div>
      </div>
    </Link>
  )
}

function Heatmap({ cells }: { cells: SectorHeatOut[] }) {
  const maxAbs = Math.max(1, ...cells.map((c) => Math.abs(c.change_pct)))
  return (
    <div style={{ background: 'var(--color-surface)', borderRadius: 22, padding: '26px 28px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 18 }}>
        <div>
          <h2 style={{ margin: '0 0 4px' }}>Where the day went, by sector</h2>
          <p style={{ fontSize: 13.5, color: 'var(--color-neutral-600)', margin: 0 }}>Average day change across every instrument on file, not just your lists.</p>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
        {cells.map((c) => {
          const intensity = Math.min(1, Math.abs(c.change_pct) / maxAbs)
          const bg = c.change_pct > 0
            ? `color-mix(in srgb, var(--color-pos-text) ${Math.round(intensity * 28)}%, var(--color-surface-2))`
            : c.change_pct < 0
              ? `color-mix(in srgb, var(--color-neg-text) ${Math.round(intensity * 28)}%, var(--color-surface-2))`
              : 'var(--color-surface-2)'
          return (
            <div key={c.sector} style={{ background: bg, borderRadius: 16, padding: '16px 17px', minHeight: 92, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{c.sector}</div>
              <div>
                <div style={{ fontFamily: 'var(--font-heading)', fontSize: 21, fontWeight: 700, color: changeVisual(c.change_pct).color }}>{fmtPct(c.change_pct)}</div>
                <div style={{ fontSize: 11.5, color: 'var(--color-neutral-600)' }}>{c.count} instruments</div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function DashboardContent({ snap }: { snap: DashboardOut }) {
  usePageHeader('Dashboard', `Close of ${snap.as_of}`)

  if (snap.watchlist_count === 0) {
    return (
      <div style={{ background: 'var(--color-surface)', borderRadius: 22, padding: '44px 32px', textAlign: 'center', maxWidth: 420, margin: '40px auto 0' }}>
        <h2 style={{ margin: '0 0 8px' }}>No watchlists yet</h2>
        <p style={{ fontSize: 14, color: 'var(--color-neutral-600)', lineHeight: 1.5, margin: '0 0 22px' }}>
          A watchlist is just a bag of symbols. Screens run against it every night.
        </p>
        <Link to="/watchlists" className="btn btn-primary">Build your first one</Link>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
      <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', background: 'var(--color-surface)', borderRadius: 20, padding: '20px 26px' }}>
        <BreadthStat label="up today" value={snap.up_count} color="var(--color-pos-text)" />
        <BreadthStat label="down today" value={snap.down_count} color="var(--color-neg-text)" />
        <BreadthStat label="moved 2%+" value={snap.moved_2pct_count} />
        <BreadthStat label="alerts today" value={snap.alerts_today} color="var(--color-brand)" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: 22 }}>
        <div style={{ background: 'var(--color-surface)', borderRadius: 22, padding: '26px 28px' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <h2 style={{ margin: 0 }}>Movers in your lists</h2>
            <Link to="/watchlists" style={{ fontSize: 13.5, fontWeight: 600 }}>All watchlists</Link>
          </div>
          {snap.movers.length === 0 ? (
            <p style={{ fontSize: 13.5, color: 'var(--color-neutral-600)' }}>No price moves to show for your watchlisted symbols yet.</p>
          ) : (
            snap.movers.map((m) => <MoverRow key={m.instrument_id} m={m} />)
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
          <div style={{ background: 'var(--color-surface)', borderRadius: 22, padding: '24px 26px' }}>
            <h2 style={{ margin: '0 0 16px' }}>Screens that fired</h2>
            {snap.fired_screens.length === 0 ? (
              <p style={{ fontSize: 13.5, color: 'var(--color-neutral-600)', margin: 0 }}>Nothing matched tonight. Screens run again after the next close.</p>
            ) : (
              snap.fired_screens.map((s) => (
                <Link
                  key={s.screen_id}
                  to="/alerts"
                  style={{ display: 'block', background: 'var(--color-surface-2)', borderRadius: 16, padding: '14px 16px', marginBottom: 10, color: 'inherit', textDecoration: 'none' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                    <span style={{ fontSize: 14.5, fontWeight: 600 }}>{s.name}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-accent-800)', background: 'var(--color-accent-100)', borderRadius: 999, padding: '3px 9px' }}>{s.count}</span>
                  </div>
                </Link>
              ))
            )}
          </div>
        </div>
      </div>

      {snap.heatmap.length > 0 && <Heatmap cells={snap.heatmap} />}
    </div>
  )
}

export function DashboardPage() {
  usePageHeader('Dashboard')
  const { data: snap, loading, error } = useFetch<DashboardOut>('/api/dashboard')

  if (loading) return <p>Loading…</p>
  if (error) return <ErrorText>{error}</ErrorText>
  if (!snap) return <Navigate to="/watchlists" replace />

  return <DashboardContent snap={snap} />
}
