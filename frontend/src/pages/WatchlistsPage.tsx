import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Corners } from '../components/Blueprint'
import { apiFetch } from '../lib/api'
import { downloadCsv } from '../lib/csv'
import { changeVisual, ChangeGlyph, ErrorText, fmtPct, fmtPrice, trendVisual } from '../lib/format'
import { IconSearch } from '../lib/icons'
import { usePageHeader } from '../lib/pageHeader'
import { SortableTh, useSortableRows } from '../lib/sort'
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
    <div
      style={{ position: 'relative', maxWidth: 340, marginBottom: 18 }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') setResults([])
      }}
    >
      <label className="field" style={{ margin: 0 }}>
        <span className="field-label">Add symbol</span>
        <div style={{ position: 'relative' }}>
          <input
            className="input"
            placeholder="e.g. TCS, INFY"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ paddingLeft: 34 }}
          />
          <div style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--color-neutral-600)', pointerEvents: 'none' }}>
            <IconSearch size={15} aria-hidden="true" />
          </div>
        </div>
      </label>
      {results.length > 0 && (
        // Real buttons in a list, not clickable divs: Tab reaches them, Enter
        // activates them and the focus ring comes for free. A full ARIA
        // combobox would buy nothing here beyond that.
        <ul
          style={{
            position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, listStyle: 'none', margin: 0, padding: 0,
            background: 'var(--color-bg)', border: '1px solid var(--color-neutral-300)', boxShadow: 'var(--shadow-md)',
            zIndex: 'var(--z-dropdown)', maxHeight: 240, overflowY: 'auto',
          }}
        >
          {results.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => add(r.id)}
                style={{
                  width: '100%', textAlign: 'left', font: 'inherit', cursor: 'pointer', background: 'none',
                  padding: '8px 12px', display: 'flex', justifyContent: 'space-between', gap: 10,
                  border: 'none', borderBottom: '1px solid var(--color-neutral-200)',
                  transition: 'background var(--dur-fast) var(--ease-out)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-neutral-100)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'none')}
              >
                <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  <strong>{r.symbol}</strong> <span style={{ color: 'var(--color-neutral-600)' }}>{r.company_name}</span>
                </span>
                <span style={{ color: 'var(--color-neutral-600)', flexShrink: 0 }}>{r.sector}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** The two "% away from the moving average" columns are derived, not served,
 *  so they are computed once here and reused by the table, the sort and the
 *  CSV export rather than three times over. */
interface DerivedRow extends WatchlistViewRow {
  dist50: number | null
  dist200: number | null
}

function derive(row: WatchlistViewRow): DerivedRow {
  const sma50 = row.indicators?.sma_50 ?? null
  const sma200 = row.indicators?.sma_200 ?? null
  return {
    ...row,
    dist50: row.close !== null && sma50 ? ((row.close - sma50) / sma50) * 100 : null,
    dist200: row.close !== null && sma200 ? ((row.close - sma200) / sma200) * 100 : null,
  }
}

const sortValue = (row: DerivedRow, key: string): unknown => row[key as keyof DerivedRow] as unknown

export function WatchlistsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const { data: list, error: listError, reload: reloadList } = useFetch<Page<WatchlistOut>>('/api/watchlists?limit=200')
  const [activeId, setActiveId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [removing, setRemoving] = useState(false)

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

  const derived = useMemo(() => (view?.items ?? []).map(derive), [view])
  const { rows, sort, toggle } = useSortableRows(derived, sortValue)

  // Switching lists must drop the selection -- ids from the old list would
  // otherwise sit in state and get deleted from the new one.
  function selectList(id: number | null) {
    setActiveId(id)
    setSelected(new Set())
  }

  const toggleSelected = useCallback((id: number) => {
    setSelected((s) => {
      const next = new Set(s)
      if (!next.delete(id)) next.add(id)
      return next
    })
  }, [])

  async function createWatchlist(e: FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    const created = await apiFetch<WatchlistOut>('/api/watchlists', { method: 'POST', body: JSON.stringify({ name: newName.trim() }) })
    setNewName('')
    setCreating(false)
    await reloadList()
    selectList(created.id)
  }

  async function deleteActive() {
    if (!active) return
    const count = rows.length
    if (!confirm(`Delete watchlist "${active.name}"${count > 0 ? ` and its ${count} symbol${count === 1 ? '' : 's'}` : ''}?`)) return
    await apiFetch(`/api/watchlists/${active.id}`, { method: 'DELETE' })
    selectList(null)
    reloadList()
  }

  async function remove(instrumentId: number, symbol: string) {
    if (!active) return
    await apiFetch(`/api/watchlists/${active.id}/items/${instrumentId}`, { method: 'DELETE' })
    toast(`${symbol} removed`)
    reloadView()
  }

  async function removeSelected() {
    if (!active || selected.size === 0 || removing) return
    const ids = [...selected]
    if (!confirm(`Remove ${ids.length} symbol${ids.length === 1 ? '' : 's'} from "${active.name}"?`)) return
    setRemoving(true)
    // allSettled, not all: one failure must not abandon the rest, and the
    // user needs to hear about partial success rather than guess from the list.
    const results = await Promise.allSettled(
      ids.map((id) => apiFetch(`/api/watchlists/${active.id}/items/${id}`, { method: 'DELETE' })),
    )
    const failed = results.filter((r) => r.status === 'rejected').length
    setRemoving(false)
    setSelected(new Set())
    reloadView()
    toast(failed === 0 ? `Removed ${ids.length}` : `Removed ${ids.length - failed}, ${failed} failed`)
  }

  function exportRowsCsv() {
    if (!active || rows.length === 0) return
    const headers = ['Symbol', 'Company', 'Sector', 'Price', 'Day change %', 'vs SMA 50 %', 'vs SMA 200 %', 'Trend']
    const csvRows = rows.map((row) => [
      row.symbol, row.company_name, row.sector, row.close, row.day_change_pct, row.dist50, row.dist200, row.trend_state,
    ])
    downloadCsv(`${active.name.replace(/[^a-z0-9-]+/gi, '_')}-${new Date().toISOString().slice(0, 10)}.csv`, headers, csvRows)
  }

  const allSelected = rows.length > 0 && selected.size === rows.length

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16, alignItems: 'center' }}>
        {watchlists.map((w) => {
          const isActive = w.id === activeId
          return (
            <button
              key={w.id}
              type="button"
              onClick={() => selectList(w.id)}
              aria-current={isActive ? 'true' : undefined}
              style={{
                padding: '6px 14px', fontFamily: 'var(--font-body)', fontSize: 13, fontWeight: 600, cursor: 'pointer',
                border: `1px solid ${isActive ? 'var(--color-accent-600)' : 'var(--color-neutral-300)'}`,
                background: isActive ? 'var(--color-accent-100)' : 'transparent',
                color: isActive ? 'var(--color-accent-900)' : 'var(--color-neutral-700)',
                transition: 'background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out)',
              }}
            >
              {w.name} <span style={{ opacity: 0.6, fontWeight: 400 }}>{active?.id === w.id ? rows.length : ''}</span>
            </button>
          )
        })}

        {creating ? (
          <form onSubmit={createWatchlist} style={{ display: 'flex', gap: 4 }}>
            <label className="field" style={{ margin: 0 }}>
              <span className="sr-only">New watchlist name</span>
              <input className="input" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="List name" style={{ width: 150, fontSize: 13, padding: '5px 8px', minHeight: 0 }} />
            </label>
            <button type="submit" className="btn btn-primary blueprint" style={{ fontSize: 12, padding: '4px 10px' }}>
              <Corners />
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

      {listError && <ErrorText>Couldn't load your watchlists: {listError}</ErrorText>}
      {!listError && !active && watchlists.length === 0 && <p className="text-muted" style={{ fontSize: 13 }}>No watchlists yet — create one above.</p>}

      {active && (
        <>
          <AddInstrument watchlistId={active.id} onAdded={reloadView} />

          {viewError ? (
            <ErrorText>Couldn't load this watchlist: {viewError}</ErrorText>
          ) : rows.length === 0 ? (
            <div className="card blueprint" style={{ maxWidth: 480, padding: 28 }}>
              <Corners />
              <div className="card-kicker">{active.name}</div>
              <div className="card-title">Nothing in this list yet</div>
              <p className="card-body">Search for a symbol above, or run a screen and add matches straight from the results.</p>
              <button type="button" className="btn btn-primary blueprint" onClick={() => navigate('/screener')} style={{ whiteSpace: 'nowrap', alignSelf: 'flex-start' }}>
                <Corners />
                Go to Screener
              </button>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                {selected.size > 0 && (
                  <button
                    type="button" className="btn btn-ghost" disabled={removing}
                    style={{ fontSize: 12.5, padding: 0, color: 'var(--color-neg-text)', marginRight: 'auto' }}
                    onClick={removeSelected}
                  >
                    {removing ? 'Removing…' : `Remove ${selected.size} selected`}
                  </button>
                )}
                <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0 }} onClick={exportRowsCsv}>
                  Export CSV
                </button>
              </div>
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      <th style={{ width: 30 }}>
                        <input
                          type="checkbox"
                          checked={allSelected}
                          aria-label={allSelected ? 'Clear selection' : 'Select all rows'}
                          onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.instrument_id)))}
                        />
                      </th>
                      <SortableTh label="Symbol" sortKey="symbol" sort={sort} onSort={toggle} />
                      <SortableTh label="Sector" sortKey="sector" sort={sort} onSort={toggle} />
                      <SortableTh label="Price" sortKey="close" sort={sort} onSort={toggle} numeric />
                      <SortableTh label="Day change" sortKey="day_change_pct" sort={sort} onSort={toggle} numeric />
                      <SortableTh label="vs SMA 50" sortKey="dist50" sort={sort} onSort={toggle} numeric />
                      <SortableTh label="vs SMA 200" sortKey="dist200" sort={sort} onSort={toggle} numeric />
                      <SortableTh label="Trend" sortKey="trend_state" sort={sort} onSort={toggle} />
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const chg = changeVisual(row.day_change_pct)
                      const d50v = changeVisual(row.dist50)
                      const d200v = changeVisual(row.dist200)
                      const tv = trendVisual(row.trend_state)
                      const isSelected = selected.has(row.instrument_id)
                      return (
                        <tr
                          key={row.instrument_id}
                          style={{ background: isSelected ? 'var(--color-accent-100)' : undefined }}
                        >
                          <td onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleSelected(row.instrument_id)}
                              aria-label={`Select ${row.symbol}`}
                            />
                          </td>
                          <td>
                            {/* A real link, matching the Screener and Scan
                                tables -- the row used to navigate on click
                                only, which no keyboard user could reach. */}
                            <Link to={`/stocks/${row.instrument_id}`} state={{ from: '/watchlists', fromLabel: active.name }}>
                              <strong>{row.symbol}</strong>
                            </Link>
                            <div style={{ fontSize: 12, color: 'var(--color-neutral-600)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {row.company_name}
                            </div>
                          </td>
                          <td>{row.sector ? <span className="tag tag-outline" style={{ whiteSpace: 'nowrap' }}>{row.sector}</span> : <span className="text-muted">—</span>}</td>
                          <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(row.close)}</td>
                          <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: chg.color }}>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                              <ChangeGlyph v={chg} />
                              {fmtPct(row.day_change_pct)}
                            </span>
                          </td>
                          <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: d50v.color }}>{fmtPct(row.dist50)}</td>
                          <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: d200v.color }}>{fmtPct(row.dist200)}</td>
                          <td>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', fontSize: 12, fontWeight: 600, border: `1px solid ${tv.border}`, color: tv.color, background: tv.bg, whiteSpace: 'nowrap' }}>
                              <ChangeGlyph v={tv} />
                              {tv.label}
                            </span>
                          </td>
                          <td>
                            <button
                              type="button" className="btn btn-icon"
                              aria-label={`Remove ${row.symbol} from ${active.name}`}
                              onClick={() => remove(row.instrument_id, row.symbol)}
                            >
                              ×
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
