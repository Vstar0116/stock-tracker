import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import { downloadCsv } from '../lib/csv'
import { changeVisual, ChangeGlyph, fmtPct, fmtPrice, trendVisual } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { useToast } from '../lib/toast'
import { useFetch } from '../lib/useFetch'
import type { InstrumentOut, Page, WatchlistOut, WatchlistViewRow } from '../lib/types'

function AddInstrument({ watchlistId, onAdded }: { watchlistId: number; onAdded: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<InstrumentOut[]>([])

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([])
      return
    }
    const id = setTimeout(() => {
      apiFetch<Page<InstrumentOut>>(`/api/instruments?q=${encodeURIComponent(query)}&limit=6`)
        .then((res) => setResults(res.items))
        .catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(id)
  }, [query])

  async function add(instrumentId: number) {
    try {
      await apiFetch(`/api/watchlists/${watchlistId}/items`, { method: 'POST', body: JSON.stringify({ instrument_id: instrumentId }) })
      setQuery('')
      setResults([])
      onAdded()
    } catch {
      // 409 (already in list) or similar -- nothing to show, just clear the box
      setQuery('')
      setResults([])
    }
  }

  return (
    <div style={{ position: 'relative', maxWidth: 340, marginBottom: 18 }}>
      <div className="field" style={{ margin: 0 }}>
        <input
          className="input"
          placeholder="Add a symbol to this watchlist…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ paddingLeft: 34 }}
        />
      </div>
      <div style={{ position: 'absolute', left: 11, top: 11, color: 'var(--color-neutral-500)', pointerEvents: 'none' }}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
      </div>
      {results.length > 0 && (
        <div style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, background: 'var(--color-bg)', border: '1px solid var(--color-neutral-300)', boxShadow: 'var(--shadow-md)', zIndex: 5, maxHeight: 240, overflowY: 'auto' }}>
          {results.map((r) => (
            <div
              key={r.id}
              onClick={() => add(r.id)}
              style={{ padding: '8px 12px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', gap: 10, borderBottom: '1px solid var(--color-neutral-200)' }}
            >
              <span>
                <strong>{r.symbol}</strong> <span style={{ color: 'var(--color-neutral-600)' }}>{r.company_name}</span>
              </span>
              <span style={{ color: 'var(--color-neutral-500)' }}>{r.sector}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function WatchlistsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const { data: list, error: listError, reload: reloadList } = useFetch<Page<WatchlistOut>>('/api/watchlists?limit=200')
  const [activeId, setActiveId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  const watchlists = list?.items ?? []
  useEffect(() => {
    if (activeId === null && watchlists.length > 0) setActiveId(watchlists[0].id)
    if (activeId !== null && !watchlists.some((w) => w.id === activeId)) setActiveId(watchlists[0]?.id ?? null)
  }, [watchlists, activeId])

  const active = watchlists.find((w) => w.id === activeId) ?? null
  usePageHeader(active ? active.name : 'Watchlists')

  const { data: view, error: viewError, reload: reloadView } = useFetch<Page<WatchlistViewRow>>(
    activeId !== null ? `/api/watchlists/${activeId}/view?limit=200` : null,
    [activeId],
  )

  async function createWatchlist(e: FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    const created = await apiFetch<WatchlistOut>('/api/watchlists', { method: 'POST', body: JSON.stringify({ name: newName.trim() }) })
    setNewName('')
    setCreating(false)
    await reloadList()
    setActiveId(created.id)
  }

  async function deleteActive() {
    if (!active || !confirm(`Delete watchlist "${active.name}"?`)) return
    await apiFetch(`/api/watchlists/${active.id}`, { method: 'DELETE' })
    setActiveId(null)
    reloadList()
  }

  async function remove(instrumentId: number) {
    if (!active) return
    await apiFetch(`/api/watchlists/${active.id}/items/${instrumentId}`, { method: 'DELETE' })
    reloadView()
  }

  const rows = view?.items ?? []

  function exportRowsCsv() {
    if (!active || rows.length === 0) return
    const headers = ['Symbol', 'Company', 'Sector', 'Price', 'Day change %', 'vs SMA 50 %', 'vs SMA 200 %', 'Trend']
    const csvRows = rows.map((row) => {
      const sma50 = row.indicators?.sma_50 ?? null
      const sma200 = row.indicators?.sma_200 ?? null
      const dist50 = row.close !== null && sma50 ? ((row.close - sma50) / sma50) * 100 : null
      const dist200 = row.close !== null && sma200 ? ((row.close - sma200) / sma200) * 100 : null
      return [row.symbol, row.company_name, row.sector, row.close, row.day_change_pct, dist50, dist200, row.trend_state]
    })
    downloadCsv(`${active.name.replace(/[^a-z0-9-]+/gi, '_')}-${new Date().toISOString().slice(0, 10)}.csv`, headers, csvRows)
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16, alignItems: 'center' }}>
        {watchlists.map((w) => {
          const isActive = w.id === activeId
          return (
            <button
              key={w.id}
              type="button"
              onClick={() => setActiveId(w.id)}
              style={{
                padding: '6px 14px', fontFamily: 'var(--font-body)', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                border: `1px solid ${isActive ? 'var(--color-accent-600)' : 'var(--color-neutral-300)'}`,
                background: isActive ? 'var(--color-accent-100)' : 'transparent',
                color: isActive ? 'var(--color-accent-900)' : 'var(--color-neutral-700)',
              }}
            >
              {w.name} <span style={{ opacity: 0.6, fontWeight: 400 }}>{active?.id === w.id ? rows.length : ''}</span>
            </button>
          )
        })}

        {creating ? (
          <form onSubmit={createWatchlist} style={{ display: 'flex', gap: 4 }}>
            <input className="input" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="List name" style={{ width: 150, fontSize: 13, padding: '5px 8px', minHeight: 0 }} />
            <button type="submit" className="btn btn-primary blueprint" style={{ fontSize: 12, padding: '4px 10px' }}>
              <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
              Add
            </button>
            <button type="button" className="btn btn-ghost" style={{ fontSize: 12 }} onClick={() => setCreating(false)}>Cancel</button>
          </form>
        ) : (
          <button type="button" className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => setCreating(true)}>
            + New watchlist
          </button>
        )}

        {active && (
          <button type="button" onClick={deleteActive} className="btn btn-ghost" style={{ fontSize: 12, color: 'var(--color-neg-text)', marginLeft: 'auto' }}>
            Delete "{active.name}"
          </button>
        )}
      </div>

      {listError && (
        <p style={{ fontSize: 13, color: 'var(--color-neg-text)' }}>Couldn't load your watchlists: {listError}</p>
      )}
      {!listError && !active && watchlists.length === 0 && <p className="text-muted" style={{ fontSize: 13 }}>No watchlists yet — create one above.</p>}

      {active && (
        <>
          <AddInstrument watchlistId={active.id} onAdded={reloadView} />

          {viewError ? (
            <p style={{ fontSize: 13, color: 'var(--color-neg-text)' }}>Couldn't load this watchlist: {viewError}</p>
          ) : rows.length === 0 ? (
            <div className="card blueprint" style={{ maxWidth: 480, padding: 28 }}>
              <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
              <div className="card-kicker">{active.name}</div>
              <div className="card-title">Nothing in this list yet</div>
              <p className="card-body">Search for a symbol above, or run a screen and add matches straight from the results.</p>
              <button type="button" className="btn btn-primary blueprint" onClick={() => navigate('/screener')} style={{ whiteSpace: 'nowrap' }}>
                <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
                Go to Screener
              </button>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 6 }}>
                <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0 }} onClick={exportRowsCsv}>
                  Export CSV
                </button>
              </div>
              <table className="table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Sector</th><th style={{ textAlign: 'right' }}>Price</th><th style={{ textAlign: 'right' }}>Day change</th>
                  <th style={{ textAlign: 'right' }}>vs SMA 50</th><th style={{ textAlign: 'right' }}>vs SMA 200</th><th>Trend</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const chg = changeVisual(row.day_change_pct)
                  const sma50 = row.indicators?.sma_50 ?? null
                  const sma200 = row.indicators?.sma_200 ?? null
                  const dist50 = row.close !== null && sma50 ? ((row.close - sma50) / sma50) * 100 : null
                  const dist200 = row.close !== null && sma200 ? ((row.close - sma200) / sma200) * 100 : null
                  const d50v = changeVisual(dist50)
                  const d200v = changeVisual(dist200)
                  const tv = trendVisual(row.trend_state)
                  return (
                    <tr key={row.instrument_id} onClick={() => navigate(`/stocks/${row.instrument_id}`, { state: { from: '/watchlists', fromLabel: active.name } })} style={{ cursor: 'pointer' }}>
                      <td>
                        <strong>{row.symbol}</strong>
                        <div style={{ fontSize: 12, color: 'var(--color-neutral-600)' }}>{row.company_name}</div>
                      </td>
                      <td>{row.sector ? <span className="tag tag-outline" style={{ whiteSpace: 'nowrap' }}>{row.sector}</span> : <span className="text-muted">—</span>}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(row.close)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: chg.color }}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                          <ChangeGlyph v={chg} />
                          {fmtPct(row.day_change_pct)}
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: d50v.color }}>{fmtPct(dist50)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: d200v.color }}>{fmtPct(dist200)}</td>
                      <td>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', fontSize: 12, fontWeight: 600, border: `1px solid ${tv.border}`, color: tv.color, background: tv.bg }}>
                          <ChangeGlyph v={tv} />
                          {tv.label}
                        </span>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <button type="button" className="btn btn-icon" title="Remove from watchlist" onClick={() => { remove(row.instrument_id); toast(`${row.symbol} removed`) }}>×</button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
              </table>
            </>
          )}
        </>
      )}
    </div>
  )
}
