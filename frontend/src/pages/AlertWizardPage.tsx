import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { EmptyState } from '../components/EmptyState'
import { RuleGroup } from '../components/RuleGroup'
import { apiFetch, ApiError } from '../lib/api'
import { changeVisual, ChangeGlyph, ErrorText, fmtPct, fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { applyRuleAction, screenRuleToUiTree, uiTreeToScreenRule } from '../lib/ruleTree'
import type { RuleAction } from '../lib/ruleTree'
import { TEMPLATES } from '../lib/screenTemplates'
import { useToast } from '../lib/toast'
import type { BacktestResponse, Page, ScreenMatchOut, ScreenRule, UiRuleGroup } from '../lib/types'

const STEPS = ['Start', 'Build', 'Preview', 'Backtest', 'Save'] as const
const BLANK_ROOT: UiRuleGroup = { type: 'group', op: 'AND', children: [{ type: 'rule', field: '', operator: '>', value: '' }] }

export function AlertWizardPage() {
  usePageHeader('Alert Wizard', 'Build a screen step by step, then save it to run nightly')
  const toast = useToast()
  const navigate = useNavigate()

  const [step, setStep] = useState(0)
  const [started, setStarted] = useState(false)
  const [root, setRoot] = useState<UiRuleGroup>(BLANK_ROOT)
  const [name, setName] = useState('')

  const [nlText, setNlText] = useState('')
  const [nlLoading, setNlLoading] = useState(false)

  const [results, setResults] = useState<ScreenMatchOut[] | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)

  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null)
  const [backtestLoading, setBacktestLoading] = useState(false)
  const [backtestError, setBacktestError] = useState<string | null>(null)

  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const definition = useMemo(() => uiTreeToScreenRule(root), [root])

  useEffect(() => {
    if (step !== 2 || !definition) return
    let cancelled = false
    setPreviewLoading(true)
    setPreviewError(null)
    apiFetch<Page<ScreenMatchOut>>('/api/screens/preview?limit=50', { method: 'POST', body: JSON.stringify({ definition }) })
      .then((res) => !cancelled && setResults(res.items))
      .catch((err) => {
        if (cancelled) return
        setResults(null)
        setPreviewError(err instanceof ApiError ? err.message : 'failed to check matches')
      })
      .finally(() => !cancelled && setPreviewLoading(false))
    return () => { cancelled = true }
  }, [step, definition])

  function begin(initialRoot: UiRuleGroup, initialName: string) {
    setRoot(initialRoot)
    setName(initialName)
    setStarted(true)
    setStep(1)
  }

  function mutate(path: number[], action: RuleAction, payload?: string) {
    setRoot((r) => applyRuleAction(r, path, action, payload))
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
      begin(screenRuleToUiTree(res.definition), nlText.trim())
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'failed to generate a rule from that text')
    } finally {
      setNlLoading(false)
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

  async function save() {
    if (!definition || saving) return
    setSaving(true)
    try {
      await apiFetch('/api/screens', { method: 'POST', body: JSON.stringify({ name: name.trim() || 'Untitled screen', definition }) })
      setSaved(true)
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'failed to save screen')
    } finally {
      setSaving(false)
    }
  }

  function reset() {
    setStep(0)
    setStarted(false)
    setRoot(BLANK_ROOT)
    setName('')
    setNlText('')
    setResults(null)
    setPreviewError(null)
    setBacktestResult(null)
    setBacktestError(null)
    setSaved(false)
  }

  const canAdvanceFromBuild = Boolean(definition)

  return (
    <div style={{ maxWidth: 780 }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 24, flexWrap: 'wrap' }}>
        {STEPS.map((label, i) => (
          <div
            key={label}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, fontWeight: 600,
              color: i === step ? 'var(--color-accent-800)' : i < step ? 'var(--color-neutral-600)' : 'var(--color-neutral-400)',
            }}
          >
            <span
              style={{
                width: 20, height: 20, borderRadius: 999, display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: i === step ? 'var(--color-accent-500)' : i < step ? 'var(--color-accent-100)' : 'var(--color-surface-2)',
                color: i === step ? '#fff' : 'inherit', fontSize: 11,
              }}
            >
              {i + 1}
            </span>
            {label}
            {i < STEPS.length - 1 && <span style={{ color: 'var(--color-neutral-400)', margin: '0 2px' }}>&rarr;</span>}
          </div>
        ))}
      </div>

      {step === 0 && (
        <div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
            {TEMPLATES.map((tpl) => (
              <button
                key={tpl.id}
                type="button"
                onClick={() => begin(JSON.parse(JSON.stringify(tpl.root)), tpl.name)}
                style={{
                  textAlign: 'left', maxWidth: 260, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', whiteSpace: 'normal',
                  cursor: 'pointer', borderRadius: 18, padding: '14px 18px', background: 'var(--color-surface)', border: '1px solid transparent',
                  color: 'var(--color-text)', fontFamily: 'var(--font-body)',
                }}
              >
                <span style={{ display: 'block', fontWeight: 600, fontSize: 14.5 }}>{tpl.name}</span>
                <span style={{ display: 'block', fontSize: 12.5, color: 'var(--color-neutral-600)', marginTop: 2 }}>{tpl.description}</span>
              </button>
            ))}
            <button
              type="button"
              onClick={() => begin(JSON.parse(JSON.stringify(BLANK_ROOT)), '')}
              style={{
                textAlign: 'left', maxWidth: 260, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', whiteSpace: 'normal',
                cursor: 'pointer', borderRadius: 18, padding: '14px 18px', background: 'var(--color-surface)', border: '1px dashed var(--color-neutral-400)',
                color: 'var(--color-text)', fontFamily: 'var(--font-body)',
              }}
            >
              <span style={{ display: 'block', fontWeight: 600, fontSize: 14.5 }}>Start blank</span>
              <span style={{ display: 'block', fontSize: 12.5, color: 'var(--color-neutral-600)', marginTop: 2 }}>Build a rule from scratch</span>
            </button>
          </div>

          <div className="card-kicker" style={{ marginBottom: 8 }}>or describe it in plain English</div>
          <form onSubmit={generateFromText} style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <label className="field" style={{ margin: 0, flex: 1, minWidth: 240 }}>
              <span className="sr-only">Describe a screen in plain English</span>
              <input
                className="input"
                value={nlText}
                onChange={(e) => setNlText(e.target.value)}
                placeholder="e.g. “pharma stocks below their 200 day average with RSI under 40”"
              />
            </label>
            <button type="submit" className="btn btn-secondary" disabled={nlLoading || !nlText.trim()} style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
              {nlLoading ? 'Generating…' : 'Generate & start'}
            </button>
          </form>
        </div>
      )}

      {step === 1 && (
        <div>
          <label className="field" style={{ marginBottom: 14 }}>
            <span className="field-label">Screen name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Untitled screen" />
          </label>
          <div className="card" style={{ padding: '20px 22px' }}>
            <div className="card-kicker" style={{ marginBottom: 10 }}>Rule builder</div>
            <RuleGroup group={root} path={[]} onMutate={mutate} depth={0} />
          </div>
          {!canAdvanceFromBuild && (
            <p className="text-muted" style={{ fontSize: 12.5, marginTop: 10 }}>Finish every condition (field, operator, value) to continue.</p>
          )}
        </div>
      )}

      {step === 2 && (
        <div>
          <p className="text-muted" style={{ fontSize: 12.5, marginTop: 0 }}>
            {previewLoading ? 'evaluating…' : previewError ? `couldn't check matches: ${previewError}` : `${results?.length ?? 0} stocks match today`}
          </p>
          {!previewError && results && results.length > 0 && (
            <div className="table-scroll" style={{ maxHeight: 320, overflowY: 'auto' }}>
              <table className="table">
                <thead><tr><th>Symbol</th><th>Sector</th><th style={{ textAlign: 'right' }}>Price</th><th style={{ textAlign: 'right' }}>Day change</th></tr></thead>
                <tbody>
                  {results.map((m) => {
                    const chg = changeVisual(m.day_change_pct)
                    return (
                      <tr key={m.instrument_id}>
                        <td><strong>{m.symbol}</strong></td>
                        <td>{m.sector ?? <span className="text-muted">—</span>}</td>
                        <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.close)}</td>
                        <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: chg.color }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                            <ChangeGlyph v={chg} />{fmtPct(m.day_change_pct)}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {!previewError && results && results.length === 0 && (
            <EmptyState title="No stocks currently match this rule." hint="You can still save it and continue -- it may start matching as prices move." />
          )}
        </div>
      )}

      {step === 3 && (
        <div>
          <p className="text-muted" style={{ fontSize: 12.5, marginTop: 0, maxWidth: 560 }}>
            How this rule's matches actually performed historically -- a record of the past, not a prediction or a recommendation. Optional.
          </p>
          <button type="button" className="btn btn-secondary" onClick={runBacktest} disabled={!definition || backtestLoading} style={{ marginBottom: 16 }}>
            {backtestLoading ? 'Running…' : 'Backtest last 250 trading days'}
          </button>
          {backtestError && <ErrorText>{backtestError}</ErrorText>}
          {backtestResult && backtestResult.total_matches === 0 && (
            <EmptyState title="No historical matches for this rule." hint="It never fired in the lookback window -- try loosening a condition." />
          )}
          {backtestResult && backtestResult.total_matches > 0 && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
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
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {step === 4 && !saved && (
        <div>
          <p style={{ fontSize: 14 }}>Save <strong>{name.trim() || 'Untitled screen'}</strong> — it will run every night after the pipeline and raise alerts on new matches.</p>
          <button type="button" className="btn btn-primary" onClick={save} disabled={saving || !definition}>
            {saving ? 'Saving…' : 'Save screen'}
          </button>
        </div>
      )}

      {step === 4 && saved && (
        <div>
          <p style={{ fontSize: 14 }}>Saved <strong>{name.trim() || 'Untitled screen'}</strong>. It's active and will run tonight.</p>
          <div style={{ display: 'flex', gap: 10 }}>
            <button type="button" className="btn btn-secondary" onClick={() => navigate('/screener')}>View in Screener</button>
            <button type="button" className="btn btn-ghost" onClick={reset}>Create another</button>
          </div>
        </div>
      )}

      {started && !(step === 4 && saved) && (
        <div style={{ display: 'flex', gap: 10, marginTop: 24 }}>
          {step > 0 && (
            <button type="button" className="btn btn-secondary" onClick={() => setStep((s) => s - 1)}>Back</button>
          )}
          {step < 4 && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setStep((s) => s + 1)}
              disabled={step === 1 && !canAdvanceFromBuild}
            >
              {step === 3 ? 'Skip to Save' : 'Next'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
