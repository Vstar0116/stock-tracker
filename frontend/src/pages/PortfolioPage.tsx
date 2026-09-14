import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, ApiError } from '../lib/api'
import { EmptyState } from '../components/EmptyState'
import { ChangeGlyph, changeVisual, ErrorText, fmtPct, fmtPrice } from '../lib/format'
import { IconSearch } from '../lib/icons'
import { usePageHeader } from '../lib/pageHeader'
import { useToast } from '../lib/toast'
import { useFetch } from '../lib/useFetch'
import type { HoldingRow, InstrumentOut, Page, PortfolioOut } from '../lib/types'

function AddHolding({ onSaved }: { onSaved: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<InstrumentOut[]>([])
  const [picked, setPicked] = useState<InstrumentOut | null>(null)
  const [quantity, setQuantity] = useState('')
  const [avgCost, setAvgCost] = useState('')
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  useEffect(() => {
    if (picked || query.trim().length < 2) {
      setResults([])
      return
    }
    const id = setTimeout(() => {
      apiFetch<Page<InstrumentOut>>(`/api/instruments?q=${encodeURIComponent(query)}&limit=6`)
        .then((res) => setResults(res.items))
        .catch(() => setResults([]))
    }, 250)
    return () => clearTimeout(id)
  }, [query, picked])

  async function save() {
    if (!picked || saving) return
    const q = Number(quantity)
    const c = Number(avgCost)
    if (!(q > 0) || !(c > 0)) {
      toast('Enter a quantity and average cost greater than zero')
      return
    }
    setSaving(true)
    try {
      await apiFetch<PortfolioOut>('/api/portfolio', {
        method: 'POST',
        body: JSON.stringify({ instrument_id: picked.id, quantity: q, avg_cost: c }),
      })
      onSaved()
      setPicked(null)
      setQuery('')
      setQuantity('')
      setAvgCost('')
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'Could not save that holding')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card" style={{ marginBottom: 20, maxWidth: 560 }}>
      <div className="card-kicker">Add a holding</div>
      {!picked ? (
        <div style={{ position: 'relative' }}>
          <label className="field" style={{ margin: 0 }}>
            <span className="field-label">Symbol</span>
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
            <ul style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, right: 0, listStyle: 'none', margin: 0, padding: 6, background: 'var(--color-surface-2)', borderRadius: 14, zIndex: 'var(--z-dropdown)', maxHeight: 240, overflowY: 'auto' }}>
              {results.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setPicked(r)
                      setResults([])
                    }}
                    style={{ width: '100%', textAlign: 'left', font: 'inherit', cursor: 'pointer', background: 'none', padding: '8px 10px', borderRadius: 10, border: 'none' }}
                  >
                    <strong>{r.symbol}</strong> <span style={{ color: 'var(--color-neutral-600)' }}>{r.company_name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ fontSize: 14 }}>
            <strong>{picked.symbol}</strong>
            <button type="button" className="btn btn-ghost" onClick={() => setPicked(null)} style={{ marginLeft: 8, fontSize: 12.5, padding: '2px 8px' }}>
              change
            </button>
          </div>
          <label className="field" style={{ margin: 0, width: 140 }}>
            <span className="field-label">Quantity</span>
            <input className="input" type="number" min="0" step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          </label>
          <label className="field" style={{ margin: 0, width: 140 }}>
            <span className="field-label">Avg. cost</span>
            <input className="input" type="number" min="0" step="any" value={avgCost} onChange={(e) => setAvgCost(e.target.value)} />
          </label>
          <button type="button" className="btn btn-primary" onClick={save} disabled={saving} style={{ marginBottom: 0 }}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      )}
    </div>
  )
}

function AllocationBar({ allocation }: { allocation: PortfolioOut['allocation'] }) {
  if (allocation.length === 0) return null
  const colors = ['var(--color-brand)', 'var(--color-accent-700)', 'var(--color-pos-text)', 'var(--color-warn-text)', 'var(--color-neutral-500)']
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 className="section-label">Sector allocation</h2>
      <div style={{ display: 'flex', height: 10, borderRadius: 999, overflow: 'hidden', marginBottom: 12 }}>
        {allocation.map((a, i) => (
          <div key={a.sector} style={{ width: `${a.pct_of_portfolio}%`, background: colors[i % colors.length] }} title={`${a.sector}: ${fmtPct(a.pct_of_portfolio)}`} />
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
        {allocation.map((a, i) => (
          <div key={a.sector} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5 }}>
            <span style={{ width: 9, height: 9, borderRadius: 999, background: colors[i % colors.length], flex: 'none' }} />
            {a.sector} <span style={{ color: 'var(--color-neutral-600)' }}>{a.pct_of_portfolio.toFixed(1)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function HoldingsTable({ rows, onDeleted }: { rows: HoldingRow[]; onDeleted: () => void }) {
  const toast = useToast()
  const [deletingId, setDeletingId] = useState<number | null>(null)

  async function remove(id: number) {
    setDeletingId(id)
    try {
      await apiFetch<PortfolioOut>(`/api/portfolio/${id}`, { method: 'DELETE' })
      onDeleted()
    } catch {
      toast('Could not remove that holding')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Sector</th>
            <th>Qty</th>
            <th>Avg. cost</th>
            <th>Last close</th>
            <th>Market value</th>
            <th>Unrealized P&amp;L</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => {
            const v = changeVisual(h.unrealized_pnl)
            return (
              <tr key={h.id}>
                <td>
                  <Link to={`/stocks/${h.instrument_id}`} state={{ from: '/portfolio', fromLabel: 'Portfolio' }}>
                    <strong>{h.symbol}</strong>
                  </Link>
                  <div style={{ fontSize: 12, color: 'var(--color-neutral-600)' }}>{h.company_name}</div>
                </td>
                <td>{h.sector ? <span className="tag tag-neutral">{h.sector}</span> : '—'}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{h.quantity}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(h.avg_cost)}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(h.close)}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(h.market_value)}</td>
                <td style={{ color: v.color, fontVariantNumeric: 'tabular-nums' }}>
                  {h.unrealized_pnl === null ? (
                    '—'
                  ) : (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                      <ChangeGlyph v={v} /> {fmtPrice(h.unrealized_pnl)} ({fmtPct(h.unrealized_pnl_pct)})
                    </span>
                  )}
                </td>
                <td>
                  <button type="button" className="btn btn-ghost" onClick={() => remove(h.id)} disabled={deletingId === h.id} style={{ fontSize: 12.5, padding: '4px 10px' }}>
                    {deletingId === h.id ? 'Removing…' : 'Remove'}
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function PortfolioPage() {
  usePageHeader('Portfolio', 'Holdings valued against the latest close — not investment advice')
  const { data, loading, error, reload } = useFetch<PortfolioOut>('/api/portfolio')

  if (loading) {
    return (
      <div style={{ maxWidth: 1000, display: 'grid', gap: 12 }} aria-busy="true" aria-label="Loading portfolio">
        <div className="skeleton" style={{ height: 100 }} />
        <div className="skeleton" style={{ height: 300 }} />
      </div>
    )
  }
  if (error) return <ErrorText>{error}</ErrorText>
  if (!data) return null

  const totalV = changeVisual(data.total_unrealized_pnl)

  return (
    <div style={{ maxWidth: 1000 }}>
      <AddHolding onSaved={reload} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 24 }}>
        <div className="card">
          <div className="card-kicker">Market value</div>
          <div className="card-title" style={{ fontSize: 20 }}>{fmtPrice(data.total_market_value)}</div>
        </div>
        <div className="card">
          <div className="card-kicker">Cost basis</div>
          <div className="card-title" style={{ fontSize: 20 }}>{fmtPrice(data.total_cost_basis)}</div>
        </div>
        <div className="card">
          <div className="card-kicker">Unrealized P&amp;L</div>
          <div className="card-title" style={{ fontSize: 20, color: totalV.color, display: 'flex', alignItems: 'center', gap: 7 }}>
            <ChangeGlyph v={totalV} /> {fmtPrice(data.total_unrealized_pnl)}
          </div>
          <p className="card-body">{fmtPct(data.total_unrealized_pnl_pct)}</p>
        </div>
      </div>

      {data.holdings.length === 0 ? (
        <EmptyState
          title="No holdings yet"
          hint="Add what you actually own above to track its value against the latest close, unrealized P&L, and sector allocation."
        />
      ) : (
        <>
          <AllocationBar allocation={data.allocation} />
          <h2 className="section-label">Holdings</h2>
          <HoldingsTable rows={data.holdings} onDeleted={reload} />
        </>
      )}
    </div>
  )
}
