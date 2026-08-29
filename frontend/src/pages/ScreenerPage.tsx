import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { RuleGroup } from '../components/RuleGroup'
import { BlueprintButton, BlueprintCard, BlueprintCorners, DataTable, ErrorText, Field, type DataTableColumn } from '../components/ui'
import { apiFetch, ApiError } from '../lib/api'
import { changeVisual, ChangeGlyph, fmtPct, fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { queryKeys } from '../lib/queryKeys'
import { applyRuleAction, collectFields, FIELD_LABELS, screenRuleToUiTree, uiTreeToScreenRule } from '../lib/ruleTree'
import type { RuleAction } from '../lib/ruleTree'
import { useToast } from '../lib/toast'
import type { Page, ScreenMatchOut, ScreenOut, ScreenRule, UiRuleGroup, WatchlistOut } from '../lib/types'

interface Template {
  id: string
  name: string
  description: string
  root: UiRuleGroup
}

const TEMPLATES: Template[] = [
  {
    id: 'above-200dma', name: 'Above 200 DMA', description: 'Close above the 200-day moving average',
    root: { type: 'group', op: 'AND', children: [{ type: 'rule', field: 'close', operator: '>', value: 'sma_200' }] },
  },
  {
    id: 'golden-cross', name: 'Golden Cross', description: 'SMA 50 just crossed above SMA 200',
    root: { type: 'group', op: 'AND', children: [{ type: 'rule', field: 'sma_50', operator: 'crossed above', value: 'sma_200' }] },
  },
  {
    id: 'volume-breakout', name: 'Volume Breakout x3', description: 'Volume at least 3x its 20-day average',
    root: { type: 'group', op: 'AND', children: [{ type: 'rule', field: 'volume', operator: '>', value: '3 x volume_sma_20' }] },
  },
]

// TEMPLATES is a fixed, non-empty module-level literal -- this is never
// actually undefined, just something noUncheckedIndexedAccess can't prove
// from a numeric index alone.
const DEFAULT_TEMPLATE = TEMPLATES[0]!

export function ScreenerPage() {
  usePageHeader('Screener', 'Build a rule, preview matches, save it to run again')
  const toast = useToast()
  const queryClient = useQueryClient()

  const [root, setRoot] = useState<UiRuleGroup>(DEFAULT_TEMPLATE.root)
  const [name, setName] = useState(DEFAULT_TEMPLATE.name)
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(DEFAULT_TEMPLATE.id)

  const [nlText, setNlText] = useState('')

  const { data: screens, error: screensError } = useQuery({
    queryKey: queryKeys.screens.list(),
    queryFn: () => apiFetch<Page<ScreenOut>>('/api/screens?limit=200'),
  })
  const { data: watchlistPage } = useQuery({
    queryKey: queryKeys.watchlists.list(),
    queryFn: () => apiFetch<Page<WatchlistOut>>('/api/watchlists?limit=200'),
  })
  const watchlists = watchlistPage?.items ?? []

  const definition = useMemo(() => uiTreeToScreenRule(root), [root])
  const extraFields = useMemo(() => Array.from(collectFields(root)).slice(0, 4), [root])

  // Debounce the KEY, not the fetch call -- React Query re-fetches whenever
  // the queryKey changes, so waiting 400ms to update `debouncedDefinition`
  // (rather than debouncing a manual apiFetch call, as this used to) gets
  // the same "live as you type, but not on every keystroke" behavior for
  // free, plus caching: flipping back to a rule tried earlier in this
  // session is served from cache instead of re-hitting the API.
  const [debouncedDefinition, setDebouncedDefinition] = useState<ScreenRule | null>(definition)
  useEffect(() => {
    const id = setTimeout(() => setDebouncedDefinition(definition), 400)
    return () => clearTimeout(id)
  }, [definition])

  const { data: previewPage, isFetching: previewLoading, error: previewError } = useQuery({
    queryKey: queryKeys.screens.preview(debouncedDefinition),
    queryFn: () => apiFetch<Page<ScreenMatchOut>>('/api/screens/preview?limit=50', { method: 'POST', body: JSON.stringify({ definition: debouncedDefinition }) }),
    enabled: debouncedDefinition !== null,
  })
  const results = debouncedDefinition === null ? null : (previewPage?.items ?? null)

  function mutate(path: number[], action: RuleAction, payload?: string) {
    setRoot((r) => applyRuleAction(r, path, action, payload))
    setActiveTemplateId(null)
  }

  function loadTemplate(tpl: Template) {
    setRoot(JSON.parse(JSON.stringify(tpl.root)))
    setName(tpl.name)
    setActiveTemplateId(tpl.id)
  }

  const saveMutation = useMutation({
    mutationFn: (payload: { name: string; definition: ScreenRule }) =>
      apiFetch('/api/screens', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: (_data, { name: savedName }) => {
      toast(`Saved "${savedName}"`)
      queryClient.invalidateQueries({ queryKey: queryKeys.screens.all })
    },
    onError: (err) => toast(err instanceof ApiError ? err.message : 'failed to save screen'),
  })

  function saveScreen() {
    if (!definition) {
      toast('Add at least one complete condition first')
      return
    }
    saveMutation.mutate({ name: name.trim() || 'Untitled screen', definition })
  }

  const nlMutation = useMutation({
    mutationFn: (text: string) => apiFetch<{ definition: ScreenRule }>('/api/screens/from-text', { method: 'POST', body: JSON.stringify({ text }) }),
    onSuccess: (res) => {
      // Drop the generated rule into the builder for review -- never save or
      // run it automatically, the user still has to check it and hit Save.
      setRoot(screenRuleToUiTree(res.definition))
      setActiveTemplateId(null)
      toast('Rule generated — review it below before saving')
    },
    onError: (err) => toast(err instanceof ApiError ? err.message : 'failed to generate a rule from that text'),
  })

  function generateFromText(e: FormEvent) {
    e.preventDefault()
    if (!nlText.trim() || nlMutation.isPending) return
    nlMutation.mutate(nlText.trim())
  }

  const addToWatchlistMutation = useMutation({
    mutationFn: ({ watchlistId, instrumentId }: { watchlistId: number; instrumentId: number; symbol: string }) =>
      apiFetch(`/api/watchlists/${watchlistId}/items`, { method: 'POST', body: JSON.stringify({ instrument_id: instrumentId }) }),
    onSuccess: (_data, { watchlistId, symbol }) => {
      toast(`${symbol} added to ${watchlists.find((w) => w.id === watchlistId)?.name ?? 'watchlist'}`)
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.view(watchlistId) })
    },
    onError: (err, { watchlistId, symbol }) => {
      if (err instanceof ApiError && err.status === 409) toast(`${symbol} already in ${watchlists.find((w) => w.id === watchlistId)?.name ?? 'watchlist'}`)
    },
  })

  const previewColumns: DataTableColumn<ScreenMatchOut>[] = [
    { header: 'Symbol', render: (m) => <Link to={`/stocks/${m.instrument_id}`} state={{ from: '/screener', fromLabel: 'Screener results' }}><strong>{m.symbol}</strong></Link> },
    { header: 'Sector', render: (m) => (m.sector ? <span className="tag tag-outline" style={{ whiteSpace: 'nowrap' }}>{m.sector}</span> : <span className="text-muted">—</span>) },
    { header: 'Price', align: 'right', render: (m) => fmtPrice(m.close) },
    {
      header: 'Day change',
      align: 'right',
      render: (m) => {
        const chg = changeVisual(m.day_change_pct)
        return (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end', color: chg.color }}>
            <ChangeGlyph v={chg} />{fmtPct(m.day_change_pct)}
          </span>
        )
      },
    },
    ...extraFields.map((f): DataTableColumn<ScreenMatchOut> => ({
      header: FIELD_LABELS[f] ?? f,
      align: 'right',
      render: (m) => {
        const v = m.values[f]
        return typeof v === 'number' ? v.toFixed(2) : (v ?? '—')
      },
    })),
    {
      header: '',
      stopRowClick: true,
      render: (m) => (
        <select
          className="input"
          defaultValue=""
          onChange={(e) => {
            const v = e.target.value
            if (v) {
              addToWatchlistMutation.mutate({ watchlistId: Number(v), instrumentId: m.instrument_id, symbol: m.symbol })
              e.target.value = ''
            }
          }}
          style={{ fontSize: 12, padding: '4px 24px 4px 8px', width: 150 }}
        >
          <option value="">Add to watchlist…</option>
          {watchlists.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 980 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
        {TEMPLATES.map((tpl) => (
          <button
            key={tpl.id}
            type="button"
            className={tpl.id === activeTemplateId ? 'btn btn-primary blueprint' : 'btn btn-secondary'}
            onClick={() => loadTemplate(tpl)}
            style={{ textAlign: 'left', maxWidth: 260 }}
          >
            {tpl.id === activeTemplateId && <BlueprintCorners />}
            <span style={{ display: 'block', fontWeight: 600, whiteSpace: 'nowrap' }}>{tpl.name}</span>
            <span style={{ display: 'block', fontSize: 11.5, fontWeight: 400, opacity: 0.75, marginTop: 2 }}>{tpl.description}</span>
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 18, flexWrap: 'wrap' }}>
        <Field label="Screen name" style={{ width: 280 }}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <BlueprintButton onClick={saveScreen} disabled={saveMutation.isPending} style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
          Save screen
        </BlueprintButton>
      </div>

      <form onSubmit={generateFromText} style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        <input
          className="input"
          value={nlText}
          onChange={(e) => setNlText(e.target.value)}
          placeholder="Describe a screen in plain English, e.g. “pharma stocks below their 200 day average with RSI under 40”"
          style={{ flex: 1 }}
        />
        <button type="submit" className="btn btn-secondary" disabled={nlMutation.isPending || !nlText.trim()} style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
          {nlMutation.isPending ? 'Generating…' : 'Generate rule'}
        </button>
      </form>

      <BlueprintCard style={{ padding: 16, marginBottom: 22 }}>
        <div className="card-kicker" style={{ marginBottom: 10 }}>Rule builder</div>
        <RuleGroup group={root} path={[]} onMutate={mutate} depth={0} />
      </BlueprintCard>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <h5 style={{ margin: 0 }}>Preview results</h5>
        <span style={{ fontSize: 12.5, color: previewError ? 'var(--color-neg-text)' : 'var(--color-neutral-600)' }}>
          {previewLoading
            ? 'evaluating…'
            : previewError
              ? `couldn't check matches: ${previewError instanceof ApiError ? previewError.message : 'failed to check matches'}`
              : results
                ? `${results.length} matches, live as you edit the rule`
                : 'add a condition to see matches'}
        </span>
      </div>

      {!previewError && results && results.length > 0 && <DataTable columns={previewColumns} rows={results} rowKey={(m) => m.instrument_id} />}
      {!previewError && results && results.length === 0 && (
        <div style={{ padding: 26, textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 13, border: '1px solid var(--color-neutral-300)' }}>
          No stocks currently match this rule.
        </div>
      )}

      {screensError && <ErrorText style={{ marginTop: 32 }}>Couldn't load saved screens: {screensError instanceof ApiError ? screensError.message : 'failed to load'}</ErrorText>}
      {!screensError && screens && screens.items.length > 0 && (
        <div style={{ marginTop: 32 }}>
          <h5 style={{ margin: '0 0 8px' }}>Saved screens</h5>
          <SavedScreensList screens={screens.items} />
        </div>
      )}
    </div>
  )
}

function SavedScreensList({ screens }: { screens: ScreenOut[] }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [runningId, setRunningId] = useState<number | null>(null)
  const [matches, setMatches] = useState<Record<number, ScreenMatchOut[]>>({})

  // Kept as a plain async function + local state rather than useMutation:
  // unlike toggleActive/remove below, this result isn't shared server state
  // that any other query depends on (nothing to invalidate), and several of
  // these can be "running" independently at once per screen -- a single
  // useMutation's isPending/data is one global slot, not naturally keyed
  // per screen id.
  async function run(screen: ScreenOut) {
    setRunningId(screen.id)
    try {
      const res = await apiFetch<Page<ScreenMatchOut>>(`/api/screens/${screen.id}/run`, { method: 'POST' })
      setMatches((m) => ({ ...m, [screen.id]: res.items }))
    } finally {
      setRunningId(null)
    }
  }

  const toggleActiveMutation = useMutation({
    mutationFn: (screen: ScreenOut) => apiFetch(`/api/screens/${screen.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !screen.is_active }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.screens.all }),
  })

  const removeMutation = useMutation({
    mutationFn: (screen: ScreenOut) => apiFetch(`/api/screens/${screen.id}`, { method: 'DELETE' }),
    onSuccess: (_data, screen) => {
      toast(`Deleted "${screen.name}"`)
      queryClient.invalidateQueries({ queryKey: queryKeys.screens.all })
    },
  })

  function remove(screen: ScreenOut) {
    if (confirm(`Delete screen "${screen.name}"?`)) removeMutation.mutate(screen)
  }

  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, border: '1px solid var(--color-divider)' }}>
      {screens.map((s) => {
        const screenMatches = matches[s.id]
        return (
          <li key={s.id} style={{ padding: '10px 14px', borderBottom: '1px solid var(--color-divider)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>
                <span className="tag tag-neutral">{s.name}</span>
                {!s.is_active && <span className="text-muted" style={{ fontSize: 11, marginLeft: 8 }}>inactive</span>}
              </span>
              <div style={{ display: 'flex', gap: 12, fontSize: 12.5 }}>
                <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0 }} onClick={() => run(s)} disabled={runningId === s.id}>
                  {runningId === s.id ? 'Running…' : 'Run now'}
                </button>
                <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0 }} onClick={() => toggleActiveMutation.mutate(s)}>
                  {s.is_active ? 'Deactivate' : 'Activate'}
                </button>
                <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0, color: 'var(--color-neg-text)' }} onClick={() => remove(s)}>
                  Delete
                </button>
              </div>
            </div>
            {screenMatches && (
              <div style={{ marginTop: 8, fontSize: 13 }}>
                {screenMatches.length === 0 ? (
                  <span className="text-muted">No matches as of the latest trading day.</span>
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {screenMatches.map((m) => (
                      <Link key={m.instrument_id} to={`/stocks/${m.instrument_id}`} state={{ from: '/screener', fromLabel: s.name }} className="tag tag-accent">
                        {m.symbol}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
