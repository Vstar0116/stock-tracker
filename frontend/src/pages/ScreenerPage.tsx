import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { RuleGroup } from '../components/RuleGroup'
import { apiFetch, ApiError } from '../lib/api'
import { downloadCsv } from '../lib/csv'
import { changeVisual, ChangeGlyph, ErrorText, fmtPct, fmtPrice, FundamentalsAsOf } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { SortableTh, useSortableRows } from '../lib/sort'
import { applyRuleAction, collectFields, FIELD_LABELS, FUNDAMENTAL_FIELD_NAMES, screenRuleToUiTree, uiTreeToScreenRule } from '../lib/ruleTree'
import type { RuleAction } from '../lib/ruleTree'
import { TEMPLATES } from '../lib/screenTemplates'
import type { ScreenTemplate } from '../lib/screenTemplates'
import { useToast } from '../lib/toast'
import { useFetch } from '../lib/useFetch'
import type { BacktestResponse, Page, ScreenMatchOut, ScreenOut, ScreenRule, UiRuleGroup, WatchlistOut } from '../lib/types'

export function ScreenerPage() {
  usePageHeader('Screener', 'Build a rule, preview matches, save it to run again')
  const toast = useToast()
  // Set by the command palette (?screen=:id) so clicking a saved screen
  // there scrolls straight to it instead of landing on a bare screener page.
  const [searchParams] = useSearchParams()
  const highlightScreenId = useMemo(() => {
    const raw = searchParams.get('screen')
    return raw ? Number(raw) : null
  }, [searchParams])

  const [root, setRoot] = useState<UiRuleGroup>(TEMPLATES[0].root)
  const [name, setName] = useState(TEMPLATES[0].name)
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(TEMPLATES[0].id)
  const [saving, setSaving] = useState(false)

  const [results, setResults] = useState<ScreenMatchOut[] | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null)
  const [backtestLoading, setBacktestLoading] = useState(false)
  const [backtestError, setBacktestError] = useState<string | null>(null)

  const [nlText, setNlText] = useState('')
  const [nlLoading, setNlLoading] = useState(false)
  // "Generate rule" overwrites whatever the user built by hand, so keep the
  // previous tree to hand back. One ref is cheaper than a full undo stack.
  const undoRoot = useRef<UiRuleGroup | null>(null)
  const [undoAvailable, setUndoAvailable] = useState(false)

  const { data: screens, error: screensError, reload: reloadScreens } = useFetch<Page<ScreenOut>>('/api/screens?limit=200')
  const { data: watchlistPage } = useFetch<Page<WatchlistOut>>('/api/watchlists?limit=200')
  const watchlists = watchlistPage?.items ?? []

  const definition = useMemo(() => uiTreeToScreenRule(root), [root])
  const extraFields = useMemo(() => Array.from(collectFields(root)).slice(0, 4), [root])

  // The extra columns are per-rule, so a match's sortable value lives either on
  // the row itself or in its `values` bag -- hence the lookup rather than a
  // plain key index.
  const matchSortValue = useCallback((m: ScreenMatchOut, key: string): unknown => {
    if (key === 'symbol' || key === 'sector') return m[key]
    if (key === 'close' || key === 'day_change_pct') return m[key]
    return m.values[key]
  }, [])
  const resultRows = useMemo(() => results ?? [], [results])
  const { rows: sortedResults, sort, toggle } = useSortableRows(resultRows, matchSortValue)
  // fundamentals_as_of rides along on every match regardless of whether the
  // rule actually screens on a fundamentals field (compile_screen always
  // includes it, like sector/close) -- gate the badge on the rule itself, or
  // a pure price/technical screen would show a "fundamentals as of" badge
  // just because one of its matches happens to also have fundamentals on
  // file, which has nothing to do with what the rule is screening for.
  const usesFundamentals = useMemo(
    () => Array.from(collectFields(root)).some((f) => FUNDAMENTAL_FIELD_NAMES.has(f)),
    [root],
  )
  // Every match row carries the same fundamentals_as_of when it's present at
  // all (this app's fundamentals are seeded in one batch, not per-symbol on
  // different days), so the first match found is representative -- not a
  // per-row guarantee, just true of how the data is actually populated today.
  const fundamentalsAsOf = useMemo(
    () =>
      usesFundamentals
        ? (resultRows.find((m) => typeof m.values.fundamentals_as_of === 'string')?.values.fundamentals_as_of as string | undefined)
        : undefined,
    [resultRows, usesFundamentals],
  )

  useEffect(() => {
    // A stale backtest for the rule the user just edited away from is worse
    // than no backtest -- clear it whenever the rule changes, same as preview.
    setBacktestResult(null)
    setBacktestError(null)
    if (!definition) {
      setResults(null)
      setPreviewError(null)
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    setPreviewError(null)
    const id = setTimeout(() => {
      apiFetch<Page<ScreenMatchOut>>('/api/screens/preview?limit=50', { method: 'POST', body: JSON.stringify({ definition }) })
        .then((res) => !cancelled && setResults(res.items))
        .catch((err) => {
          if (cancelled) return
          setResults(null)
          setPreviewError(err instanceof ApiError ? err.message : 'failed to check matches')
        })
        .finally(() => !cancelled && setPreviewLoading(false))
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [definition])

  function mutate(path: number[], action: RuleAction, payload?: string) {
    setRoot((r) => applyRuleAction(r, path, action, payload))
    setActiveTemplateId(null)
    // Once they start editing by hand, restoring the pre-generation tree would
    // throw that work away -- so the offer expires here.
    setUndoAvailable(false)
  }

  function undoGenerate() {
    if (!undoRoot.current) return
    setRoot(undoRoot.current)
    undoRoot.current = null
    setUndoAvailable(false)
    toast('Restored your previous rule')
  }

  function loadTemplate(tpl: ScreenTemplate) {
    setRoot(JSON.parse(JSON.stringify(tpl.root)))
    setName(tpl.name)
    setActiveTemplateId(tpl.id)
  }

  async function saveScreen() {
    if (!definition) {
      toast('Add at least one complete condition first')
      return
    }
    setSaving(true)
    try {
      await apiFetch('/api/screens', { method: 'POST', body: JSON.stringify({ name: name.trim() || 'Untitled screen', definition }) })
      toast(`Saved "${name.trim() || 'Untitled screen'}"`)
      reloadScreens()
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'failed to save screen')
    } finally {
      setSaving(false)
    }
  }

  async function runBacktest() {
    if (!definition || backtestLoading) return
    setBacktestLoading(true)
    setBacktestError(null)
    try {
      const res = await apiFetch<BacktestResponse>('/api/screens/backtest', { method: 'POST', body: JSON.stringify({ definition }) })
      setBacktestResult(res)
    } catch (err) {
      setBacktestResult(null)
      setBacktestError(err instanceof ApiError ? err.message : 'backtest failed')
    } finally {
      setBacktestLoading(false)
    }
  }

  async function generateFromText(e: FormEvent) {
    e.preventDefault()
    if (!nlText.trim() || nlLoading) return
    setNlLoading(true)
    try {
      const res = await apiFetch<{ definition: ScreenRule }>('/api/screens/from-text', {
        method: 'POST',
        body: JSON.stringify({ text: nlText.trim() }),
      })
      // Drop the generated rule into the builder for review -- never save or
      // run it automatically, the user still has to check it and hit Save.
      undoRoot.current = root
      setRoot(screenRuleToUiTree(res.definition))
      setActiveTemplateId(null)
      setUndoAvailable(true)
      toast('Rule generated — review it below before saving')
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'failed to generate a rule from that text')
    } finally {
      setNlLoading(false)
    }
  }

  function exportResultsCsv() {
    if (!results || results.length === 0) return
    const headers = ['Symbol', 'Sector', 'Price', 'Day change %', ...extraFields.map((f) => FIELD_LABELS[f] ?? f)]
    const rows = results.map((m) => [
      m.symbol,
      m.sector,
      m.close,
      m.day_change_pct,
      ...extraFields.map((f) => {
        const v = m.values[f]
        return typeof v === 'number' ? v : v ?? null
      }),
    ])
    downloadCsv(`${(name.trim() || 'screen').replace(/[^a-z0-9-]+/gi, '_')}-${new Date().toISOString().slice(0, 10)}.csv`, headers, rows)
  }

  async function addToWatchlist(instrumentId: number, symbol: string, watchlistId: number) {
    const wl = watchlists.find((w) => w.id === watchlistId)
    try {
      await apiFetch(`/api/watchlists/${watchlistId}/items`, { method: 'POST', body: JSON.stringify({ instrument_id: instrumentId }) })
      toast(`${symbol} added to ${wl?.name ?? 'watchlist'}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) toast(`${symbol} already in ${wl?.name ?? 'watchlist'}`)
    }
  }

  return (
    <div style={{ maxWidth: 980 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
        {TEMPLATES.map((tpl) => {
          const isActive = tpl.id === activeTemplateId
          return (
            <button
              key={tpl.id}
              type="button"
              onClick={() => loadTemplate(tpl)}
              style={{
                textAlign: 'left', maxWidth: 260, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', whiteSpace: 'normal',
                cursor: 'pointer', borderRadius: 18, padding: '14px 18px',
                background: isActive ? 'var(--color-accent-100)' : 'var(--color-surface)',
                border: `1px solid ${isActive ? 'var(--color-accent-500)' : 'transparent'}`,
                color: 'var(--color-text)', fontFamily: 'var(--font-body)',
              }}
            >
              <span style={{ display: 'block', fontWeight: 600, fontSize: 14.5 }}>{tpl.name}</span>
              <span style={{ display: 'block', fontSize: 12.5, color: 'var(--color-neutral-600)', marginTop: 2 }}>{tpl.description}</span>
            </button>
          )
        })}
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <label className="field" style={{ margin: 0, width: 280 }}>
          <span className="field-label">Screen name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="button" className="btn btn-primary" onClick={saveScreen} disabled={saving} style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
          Save screen
        </button>
      </div>

      <form onSubmit={generateFromText} style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        <label className="field" style={{ margin: 0, flex: 1, minWidth: 240 }}>
          <span className="sr-only">Describe a screen in plain English</span>
          <input
            className="input"
            value={nlText}
            onChange={(e) => setNlText(e.target.value)}
            placeholder="Describe a screen in plain English, e.g. “pharma stocks below their 200 day average with RSI under 40”"
          />
        </label>
        <button type="submit" className="btn btn-secondary" disabled={nlLoading || !nlText.trim()} style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
          {nlLoading ? 'Generating…' : 'Generate rule'}
        </button>
        {undoAvailable && (
          <button type="button" className="btn btn-ghost" onClick={undoGenerate} style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
            Undo generate
          </button>
        )}
      </form>

      <div className="card" style={{ padding: '20px 22px', marginBottom: 22 }}>
        <div className="card-kicker" style={{ marginBottom: 10 }}>Rule builder</div>
        <RuleGroup group={root} path={[]} onMutate={mutate} depth={0} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, gap: 12 }}>
        <h5 style={{ margin: 0 }}>Preview results</h5>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 12.5, color: previewError ? 'var(--color-neg-text)' : 'var(--color-neutral-600)' }}>
            {previewLoading
              ? 'evaluating…'
              : previewError
                ? `couldn't check matches: ${previewError}`
                : results
                  ? `${results.length} matches, live as you edit the rule`
                  : 'add a condition to see matches'}
          </span>
          <FundamentalsAsOf asOf={fundamentalsAsOf} />
          {!previewError && results && results.length > 0 && (
            <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0, whiteSpace: 'nowrap' }} onClick={exportResultsCsv}>
              Export CSV
            </button>
          )}
        </div>
      </div>

      {!previewError && results && results.length > 0 && (
        <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <SortableTh label="Symbol" sortKey="symbol" sort={sort} onSort={toggle} />
              <SortableTh label="Sector" sortKey="sector" sort={sort} onSort={toggle} />
              <SortableTh label="Price" sortKey="close" sort={sort} onSort={toggle} numeric />
              <SortableTh label="Day change" sortKey="day_change_pct" sort={sort} onSort={toggle} numeric />
              {extraFields.map((f) => (
                <SortableTh key={f} label={FIELD_LABELS[f] ?? f} sortKey={f} sort={sort} onSort={toggle} numeric />
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {sortedResults.map((m) => {
              const chg = changeVisual(m.day_change_pct)
              return (
                <tr key={m.instrument_id}>
                  <td><Link to={`/stocks/${m.instrument_id}`} state={{ from: '/screener', fromLabel: 'Screener results' }}><strong>{m.symbol}</strong></Link></td>
                  <td>{m.sector ? <span className="tag tag-neutral" style={{ whiteSpace: 'nowrap' }}>{m.sector}</span> : <span className="text-muted">—</span>}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.close)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: chg.color }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                      <ChangeGlyph v={chg} />{fmtPct(m.day_change_pct)}
                    </span>
                  </td>
                  {extraFields.map((f) => {
                    const v = m.values[f]
                    return <td key={f} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{typeof v === 'number' ? v.toFixed(2) : v ?? '—'}</td>
                  })}
                  <td onClick={(e) => e.stopPropagation()}>
                    <select
                      className="input"
                      aria-label={`Add ${m.symbol} to a watchlist`}
                      defaultValue=""
                      onChange={(e) => { const v = e.target.value; if (v) { addToWatchlist(m.instrument_id, m.symbol, Number(v)); e.target.value = '' } }}
                      style={{ fontSize: 12, padding: '4px 24px 4px 8px', width: 150 }}
                    >
                      <option value="">Add to watchlist…</option>
                      {watchlists.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        </div>
      )}
      {!previewError && results && results.length === 0 && (
        <EmptyState
          title="No stocks currently match this rule."
          hint="Loosen a condition, or switch the group from AND to OR to widen the net."
        />
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 28, marginBottom: 8, gap: 12, flexWrap: 'wrap' }}>
        <h5 style={{ margin: 0 }}>Backtest</h5>
        <button type="button" className="btn btn-secondary" onClick={runBacktest} disabled={!definition || backtestLoading} style={{ whiteSpace: 'nowrap' }}>
          {backtestLoading ? 'Running…' : 'Backtest last 250 trading days'}
        </button>
      </div>
      <p className="text-muted" style={{ fontSize: 12.5, margin: '0 0 12px', maxWidth: 640 }}>
        How this rule's matches actually performed historically -- a record of the past, not a prediction or a recommendation.
      </p>
      {backtestError && <ErrorText>{backtestError}</ErrorText>}
      {backtestResult && backtestResult.total_matches === 0 && (
        <EmptyState title="No historical matches for this rule." hint="It never fired in the lookback window -- try loosening a condition." />
      )}
      {backtestResult && backtestResult.total_matches > 0 && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
            {backtestResult.horizons.map((h) => (
              <div key={h.horizon_days} className="card" style={{ padding: '14px 18px', minWidth: 160 }}>
                <div className="card-kicker" style={{ marginBottom: 6 }}>{h.horizon_days}-day forward</div>
                {h.sample_size === 0 || h.avg_return_pct === null ? (
                  <div className="text-muted" style={{ fontSize: 13 }}>not enough data yet</div>
                ) : (
                  <>
                    <div style={{ fontSize: 22, fontWeight: 700, color: h.avg_return_pct >= 0 ? 'var(--color-pos-text)' : 'var(--color-neg-text)' }}>
                      {fmtPct(h.avg_return_pct)}
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--color-neutral-600)' }}>avg return &middot; {h.sample_size} matches</div>
                    <div style={{ fontSize: 12.5, color: 'var(--color-neutral-600)' }}>{h.hit_rate_pct?.toFixed(0)}% positive &middot; median {fmtPct(h.median_return_pct)}</div>
                  </>
                )}
              </div>
            ))}
          </div>
          <p className="text-muted" style={{ fontSize: 12, marginTop: 0 }}>
            {backtestResult.total_matches} matches across {backtestResult.dates_evaluated} trading days evaluated, through {backtestResult.as_of}.
          </p>
        </>
      )}

      {screensError && <ErrorText style={{ marginTop: 32 }}>Couldn't load saved screens: {screensError}</ErrorText>}
      {!screensError && screens && screens.items.length > 0 && (
        <div style={{ marginTop: 32 }}>
          <h5 style={{ margin: '0 0 8px' }}>Saved screens</h5>
          <SavedScreensList screens={screens.items} onChanged={reloadScreens} highlightId={highlightScreenId} />
        </div>
      )}
    </div>
  )
}

function SavedScreensList({
  screens,
  onChanged,
  highlightId,
}: {
  screens: ScreenOut[]
  onChanged: () => void
  highlightId?: number | null
}) {
  const toast = useToast()
  const [runningId, setRunningId] = useState<number | null>(null)
  const [matches, setMatches] = useState<Record<number, ScreenMatchOut[]>>({})
  const highlightRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (highlightId != null) highlightRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [highlightId])

  // Each of these used to let a rejection escape unhandled: the row simply
  // stopped responding and the user was never told why.
  async function run(screen: ScreenOut) {
    setRunningId(screen.id)
    try {
      const res = await apiFetch<Page<ScreenMatchOut>>(`/api/screens/${screen.id}/run`, { method: 'POST' })
      setMatches((m) => ({ ...m, [screen.id]: res.items }))
    } catch (err) {
      toast(err instanceof ApiError ? err.message : `couldn't run "${screen.name}"`)
    } finally {
      setRunningId(null)
    }
  }

  async function toggleActive(screen: ScreenOut) {
    try {
      await apiFetch(`/api/screens/${screen.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !screen.is_active }) })
      onChanged()
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'failed to update screen')
    }
  }

  async function remove(screen: ScreenOut) {
    if (!confirm(`Delete screen "${screen.name}"? Alerts already raised by it are kept.`)) return
    try {
      await apiFetch(`/api/screens/${screen.id}`, { method: 'DELETE' })
      toast(`Deleted "${screen.name}"`)
      onChanged()
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'failed to delete screen')
    }
  }

  return (
    <div>
      {screens.map((s) => (
        <div
          key={s.id}
          ref={s.id === highlightId ? highlightRef : undefined}
          style={{
            background: 'var(--color-surface)', borderRadius: 18, padding: '14px 20px', marginBottom: 10,
            outline: s.id === highlightId ? '2px solid var(--color-accent-500)' : undefined,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', justifyContent: 'space-between' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 15, fontWeight: 600 }}>{s.name}</span>
              {!s.is_active && <span className="tag tag-neutral" style={{ fontWeight: 600 }}>inactive</span>}
            </span>
            <div style={{ display: 'flex', gap: 14, fontSize: 13.5 }}>
              <button type="button" className="btn btn-ghost" style={{ fontSize: 13.5, padding: 0 }} onClick={() => run(s)} disabled={runningId === s.id}>
                {runningId === s.id ? 'Running…' : 'Run now'}
              </button>
              <button type="button" className="btn btn-ghost" style={{ fontSize: 13.5, padding: 0 }} onClick={() => toggleActive(s)}>
                {s.is_active ? 'Deactivate' : 'Activate'}
              </button>
              <button type="button" className="btn btn-ghost" style={{ fontSize: 13.5, padding: 0, color: 'var(--color-neg-text)' }} onClick={() => remove(s)}>
                Delete
              </button>
            </div>
          </div>
          {matches[s.id] && (
            <div style={{ marginTop: 10, fontSize: 13 }}>
              {matches[s.id].length === 0 ? (
                <span className="text-muted">No matches as of the latest trading day.</span>
              ) : (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {matches[s.id].map((m) => (
                    <Link key={m.instrument_id} to={`/stocks/${m.instrument_id}`} state={{ from: '/screener', fromLabel: s.name }} className="tag tag-accent">
                      {m.symbol}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
