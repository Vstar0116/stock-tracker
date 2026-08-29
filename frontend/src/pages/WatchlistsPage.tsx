import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { BlueprintButton, DataTable, EmptyState, ErrorText, type DataTableColumn } from '../components/ui'
import { apiFetch, ApiError } from '../lib/api'
import { changeVisual, ChangeGlyph, fmtPct, fmtPrice, trendVisual } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { queryKeys } from '../lib/queryKeys'
import { useToast } from '../lib/toast'
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
  const queryClient = useQueryClient()

  const { data: list, error: listError } = useQuery({
    queryKey: queryKeys.watchlists.list(),
    queryFn: () => apiFetch<Page<WatchlistOut>>('/api/watchlists?limit=200'),
  })
  const [activeId, setActiveId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')

  const watchlists = list?.items ?? []
  useEffect(() => {
    if (activeId === null && watchlists.length > 0) setActiveId(watchlists[0]?.id ?? null)
    if (activeId !== null && !watchlists.some((w) => w.id === activeId)) setActiveId(watchlists[0]?.id ?? null)
  }, [watchlists, activeId])

  const active = watchlists.find((w) => w.id === activeId) ?? null
  usePageHeader(active ? active.name : 'Watchlists')

  const { data: view, error: viewError } = useQuery({
    queryKey: queryKeys.watchlists.view(activeId ?? -1),
    queryFn: () => apiFetch<Page<WatchlistViewRow>>(`/api/watchlists/${activeId}/view?limit=200`),
    enabled: activeId !== null,
  })

  const invalidateLists = () => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all })

  const createMutation = useMutation({
    mutationFn: (createdName: string) => apiFetch<WatchlistOut>('/api/watchlists', { method: 'POST', body: JSON.stringify({ name: createdName }) }),
    onSuccess: (created) => {
      setNewName('')
      setCreating(false)
      invalidateLists()
      setActiveId(created.id)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (watchlistId: number) => apiFetch(`/api/watchlists/${watchlistId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setActiveId(null)
      invalidateLists()
    },
  })

  const removeItemMutation = useMutation({
    mutationFn: ({ watchlistId, instrumentId }: { watchlistId: number; instrumentId: number }) =>
      apiFetch(`/api/watchlists/${watchlistId}/items/${instrumentId}`, { method: 'DELETE' }),
    onSuccess: (_data, { watchlistId }) => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.view(watchlistId) }),
  })

  function createWatchlist(e: FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    createMutation.mutate(newName.trim())
  }

  function deleteActive() {
    if (!active || !confirm(`Delete watchlist "${active.name}"?`)) return
    deleteMutation.mutate(active.id)
  }

  function remove(instrumentId: number, symbol: string) {
    if (!active) return
    removeItemMutation.mutate({ watchlistId: active.id, instrumentId })
    toast(`${symbol} removed`)
  }

  const rows = view?.items ?? []

  const columns: DataTableColumn<WatchlistViewRow>[] = [
    {
      header: 'Symbol',
      render: (row) => (
        <>
          <strong>{row.symbol}</strong>
          <div style={{ fontSize: 12, color: 'var(--color-neutral-600)' }}>{row.company_name}</div>
        </>
      ),
    },
    {
      header: 'Sector',
      render: (row) => (row.sector ? <span className="tag tag-outline" style={{ whiteSpace: 'nowrap' }}>{row.sector}</span> : <span className="text-muted">—</span>),
    },
    { header: 'Price', align: 'right', render: (row) => fmtPrice(row.close) },
    {
      header: 'Day change',
      align: 'right',
      render: (row) => {
        const chg = changeVisual(row.day_change_pct)
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end', color: chg.color }}>
            <ChangeGlyph v={chg} />
            {fmtPct(row.day_change_pct)}
          </span>
        )
      },
    },
    {
      header: 'vs SMA 50',
      align: 'right',
      render: (row) => {
        const sma50 = row.indicators?.sma_50 ?? null
        const dist50 = row.close !== null && sma50 ? ((row.close - sma50) / sma50) * 100 : null
        return <span style={{ color: changeVisual(dist50).color }}>{fmtPct(dist50)}</span>
      },
    },
    {
      header: 'vs SMA 200',
      align: 'right',
      render: (row) => {
        const sma200 = row.indicators?.sma_200 ?? null
        const dist200 = row.close !== null && sma200 ? ((row.close - sma200) / sma200) * 100 : null
        return <span style={{ color: changeVisual(dist200).color }}>{fmtPct(dist200)}</span>
      },
    },
    {
      header: 'Trend',
      render: (row) => {
        const tv = trendVisual(row.trend_state)
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 9px', fontSize: 12, fontWeight: 600, border: `1px solid ${tv.border}`, color: tv.color, background: tv.bg }}>
            <ChangeGlyph v={tv} />
            {tv.label}
          </span>
        )
      },
    },
    {
      header: '',
      stopRowClick: true,
      render: (row) => (
        <button type="button" className="btn btn-icon" title="Remove from watchlist" onClick={() => remove(row.instrument_id, row.symbol)}>
          ×
        </button>
      ),
    },
  ]

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
            <BlueprintButton type="submit" style={{ fontSize: 12, padding: '4px 10px' }}>
              Add
            </BlueprintButton>
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

      {listError && <ErrorText>Couldn't load your watchlists: {listError instanceof ApiError ? listError.message : 'failed to load'}</ErrorText>}
      {!listError && !active && watchlists.length === 0 && <p className="text-muted" style={{ fontSize: 13 }}>No watchlists yet — create one above.</p>}

      {active && (
        <>
          <AddInstrument watchlistId={active.id} onAdded={() => queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.view(active.id) })} />

          {viewError ? (
            <ErrorText>Couldn't load this watchlist: {viewError instanceof ApiError ? viewError.message : 'failed to load'}</ErrorText>
          ) : rows.length === 0 ? (
            <EmptyState
              style={{ maxWidth: 480 }}
              kicker={active.name}
              title="Nothing in this list yet"
              body="Search for a symbol above, or run a screen and add matches straight from the results."
              actionLabel="Go to Screener"
              onAction={() => navigate('/screener')}
            />
          ) : (
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.instrument_id}
              onRowClick={(row) => navigate(`/stocks/${row.instrument_id}`, { state: { from: '/watchlists', fromLabel: active.name } })}
            />
          )}
        </>
      )}
    </div>
  )
}
