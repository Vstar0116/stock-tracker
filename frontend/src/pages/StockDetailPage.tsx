import { useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { TradingViewChart, TV_STUDY_OPTIONS } from '../components/TradingViewChart'
import { apiFetch, ApiError } from '../lib/api'
import { changeVisual, ChangeGlyph, ErrorText, fmtNum, fmtPct, fmtPrice, indianNum } from '../lib/format'
import { IconArrowLeft } from '../lib/icons'
import { usePageHeader } from '../lib/pageHeader'
import { useFetch } from '../lib/useFetch'
import type { CrossoverSeriesOut, InstrumentDetail, Page, PeerRow, PriceOut } from '../lib/types'

// Per-browser chart preference, not user data -- localStorage is fine here
// (unlike the auth token, this holds nothing sensitive). Wrapped because
// localStorage can throw (private browsing, disabled storage).
function readStudyPref(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}
function writeStudyPref(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // best-effort only
  }
}

function StudyPicker({ label, value, onChange, otherValue }: { label: string; value: string; onChange: (v: string) => void; otherValue: string }) {
  return (
    <label className="field" style={{ margin: 0 }}>
      <span className="field-label">{label}</span>
      <select className="input" style={{ width: 200, fontSize: 13 }} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">None</option>
        {TV_STUDY_OPTIONS.filter((s) => s.id !== otherValue).map((s) => (
          <option key={s.id} value={s.id}>{s.label}</option>
        ))}
      </select>
    </label>
  )
}

interface IndicatorRow {
  label: string
  value: string
  flag?: { label: string; bg: string; color: string } | null
}

function IndicatorCard({ kicker, rows }: { kicker: string; rows: IndicatorRow[] }) {
  return (
    <div className="card" style={{ padding: '20px 22px' }}>
      <div className="card-kicker">{kicker}</div>
      {rows.map((it) => (
        <div key={it.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid var(--color-neutral-200)', fontSize: 13 }}>
          <span style={{ color: 'var(--color-neutral-600)' }}>{it.label}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {it.flag && (
              <span style={{ fontSize: 10.5, fontWeight: 600, padding: '1px 6px', background: it.flag.bg, color: it.flag.color }}>{it.flag.label}</span>
            )}
            <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{it.value}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

function CustomCrossoverCard({ instrumentId }: { instrumentId: number }) {
  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<'sma' | 'ema'>('ema')
  const [result, setResult] = useState<CrossoverSeriesOut | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fastNum = Number(fast)
  const slowNum = Number(slow)
  const invalid = !Number.isInteger(fastNum) || !Number.isInteger(slowNum) || fastNum < 1 || fastNum >= slowNum || slowNum > 400

  async function run() {
    if (invalid) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch<CrossoverSeriesOut>(
        `/api/instruments/${instrumentId}/crossover?fast=${fastNum}&slow=${slowNum}&ma_type=${maType}`,
      )
      setResult(res)
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err.message : 'failed to compute crossover')
    } finally {
      setLoading(false)
    }
  }

  const last = result?.points[result.points.length - 1] ?? null

  return (
    <div className="card" style={{ padding: '20px 22px' }}>
      <div className="card-kicker">Custom crossover</div>
      <div style={{ display: 'flex', gap: 6, alignItems: 'flex-end', marginBottom: 10, flexWrap: 'wrap' }}>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label" style={{ fontSize: 10.5 }}>Fast</span>
          <input className="input" type="number" min={1} max={400} style={{ width: 64, fontSize: 13, padding: '5px 8px' }} value={fast} onChange={(e) => setFast(e.target.value)} />
        </label>
        <span style={{ color: 'var(--color-neutral-600)', paddingBottom: 8 }} aria-hidden="true">/</span>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label" style={{ fontSize: 10.5 }}>Slow</span>
          <input className="input" type="number" min={2} max={400} style={{ width: 64, fontSize: 13, padding: '5px 8px' }} value={slow} onChange={(e) => setSlow(e.target.value)} />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span className="sr-only">Moving average type</span>
          <select className="input" style={{ width: 84, fontSize: 13, padding: '5px 8px' }} value={maType} onChange={(e) => setMaType(e.target.value as 'sma' | 'ema')}>
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </label>
        <button type="button" className="btn btn-secondary" style={{ fontSize: 12.5, padding: '5px 12px' }} onClick={run} disabled={invalid || loading}>
          {loading ? 'Computing…' : 'Compute'}
        </button>
      </div>
      {invalid && <ErrorText style={{ fontSize: 12 }}>Fast must be a whole number below slow, and slow at most 400.</ErrorText>}
      {error && <ErrorText style={{ fontSize: 12 }}>{error}</ErrorText>}
      {last && (
        <div style={{ fontSize: 13 }}>
          <div>Fast ({fastNum}): <strong>{last.fast?.toFixed(2) ?? '—'}</strong></div>
          <div>Slow ({slowNum}): <strong>{last.slow?.toFixed(2) ?? '—'}</strong></div>
          <div style={{ marginTop: 6 }}>
            {last.signal ? (
              <span className="tag tag-accent">{last.signal === 'crossed_above' ? 'Crossed above' : 'Crossed below'} as of {last.trade_date}</span>
            ) : (
              <span className="text-muted">No crossover on the latest bar</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** Plain SVG line chart drawn from the price history the page already
 *  fetches -- no charting dependency, matches the redesign's "native" chart
 *  mode. Skips a moving-average overlay: the API only serves the latest
 *  SMA/EMA values, not a per-day series, so there's nothing to draw a second
 *  line from. Upgrade to TradingView's studies (already the "embed" mode)
 *  covers that until a per-day indicator series exists. */
function NativeChart({ prices }: { prices: PriceOut[] }) {
  if (prices.length < 2) {
    return (
      <div style={{ height: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-neutral-600)', fontSize: 13 }}>
        Not enough price history to chart yet.
      </div>
    )
  }
  const W = 720
  const H = 300
  const PAD = 8
  const closes = prices.map((p) => p.adjusted_close)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || 1
  const x = (i: number) => (i / (prices.length - 1)) * W
  const y = (v: number) => PAD + (1 - (v - min) / span) * (H - PAD * 2)
  const linePath = closes.map((c, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(c).toFixed(1)}`).join(' ')
  const areaPath = `${linePath} L ${x(prices.length - 1).toFixed(1)} ${H} L 0 ${H} Z`
  const lastX = x(prices.length - 1)
  const lastY = y(closes[closes.length - 1])

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id="detailFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-brand)" stopOpacity={0.34} />
            <stop offset="100%" stopColor="var(--color-brand)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <g stroke="var(--color-divider)" strokeWidth={1}>
          <line x1={0} y1={H * 0.2} x2={W} y2={H * 0.2} />
          <line x1={0} y1={H * 0.43} x2={W} y2={H * 0.43} />
          <line x1={0} y1={H * 0.67} x2={W} y2={H * 0.67} />
          <line x1={0} y1={H * 0.9} x2={W} y2={H * 0.9} />
        </g>
        <path d={areaPath} fill="url(#detailFill)" />
        <path d={linePath} fill="none" stroke="var(--color-accent-700)" strokeWidth={2.4} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={lastX} cy={lastY} r={5} fill="var(--color-accent-700)" stroke="var(--color-surface)" strokeWidth={3} />
      </svg>
      <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap', marginTop: 16, fontSize: 12.5, color: 'var(--color-neutral-600)' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <span style={{ width: 16, height: 2.5, background: 'var(--color-accent-700)', display: 'block', borderRadius: 2 }} /> Close
        </span>
        <span style={{ marginLeft: 'auto' }}>{prices.length} sessions to {prices[prices.length - 1].trade_date} · adjusted for corporate actions</span>
      </div>
    </div>
  )
}

const PEER_COLUMNS: { key: keyof PeerRow; label: string; decimals?: number; prefix?: string }[] = [
  { key: 'cmp', label: 'CMP', prefix: '₹' },
  { key: 'market_cap', label: 'Mkt Cap Cr.', decimals: 0 },
  { key: 'pe', label: 'P/E' },
  { key: 'roce', label: 'ROCE %' },
  { key: 'debt_to_equity', label: 'Debt/Eq' },
  { key: 'peg', label: 'PEG' },
  { key: 'eps_diluted', label: 'EPS' },
  { key: 'eps_growth', label: 'EPS Growth %' },
  { key: 'fcf_per_share', label: 'FCF/Share' },
  { key: 'fcf_conversion', label: 'FCF Conv. %' },
]

function readPeerColumnPref(): Set<string> {
  try {
    const raw = localStorage.getItem('peer-columns')
    return raw ? new Set(JSON.parse(raw)) : new Set(PEER_COLUMNS.map((c) => c.key))
  } catch {
    return new Set(PEER_COLUMNS.map((c) => c.key))
  }
}
function writePeerColumnPref(cols: Set<string>) {
  try {
    localStorage.setItem('peer-columns', JSON.stringify([...cols]))
  } catch {
    // best-effort only
  }
}

function median(values: number[]): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/** Only renders once the backend has returned peers -- it returns [] whenever
 *  the instrument has no curated sunrise sector or no fundamentals of its
 *  own, so most of the market (fundamentals are manual-entry, curated-subset
 *  only) simply shows nothing here rather than an empty table. */
function PeerComparisonCard({ instrumentId }: { instrumentId: number }) {
  const { data: peers } = useFetch<PeerRow[]>(`/api/instruments/${instrumentId}/peers`, [instrumentId])
  const [visibleCols, setVisibleCols] = useState<Set<string>>(() => readPeerColumnPref())
  const [editingCols, setEditingCols] = useState(false)

  function toggleCol(key: string) {
    setVisibleCols((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      writePeerColumnPref(next)
      return next
    })
  }

  if (!peers || peers.length === 0) return null

  const cols = PEER_COLUMNS.filter((c) => visibleCols.has(c.key))

  return (
    <div className="card" style={{ padding: '20px 22px', marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <div className="card-kicker" style={{ margin: 0 }}>Peer comparison</div>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 12px' }} onClick={() => setEditingCols((v) => !v)}>
          {editingCols ? 'Done' : 'Edit columns'}
        </button>
      </div>
      {editingCols && (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14, padding: '10px 12px', background: 'var(--color-surface-2)', borderRadius: 8, fontSize: 12.5 }}>
          {PEER_COLUMNS.map((c) => (
            <label key={c.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer' }}>
              <input type="checkbox" checked={visibleCols.has(c.key)} onChange={() => toggleCol(c.key)} />
              {c.label}
            </label>
          ))}
        </div>
      )}
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Company</th>
              {cols.map((c) => <th key={c.key} style={{ textAlign: 'right' }}>{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {peers.map((p) => (
              <tr
                key={p.instrument_id}
                style={p.instrument_id === instrumentId ? { background: 'var(--color-surface-2)', fontWeight: 600 } : undefined}
                title={`Fundamentals as of ${p.fundamentals_as_of}`}
              >
                <td>{p.symbol}</td>
                {cols.map((c) => (
                  <td key={c.key} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {p[c.key] === null ? '—' : `${c.prefix ?? ''}${fmtNum(p[c.key] as number, c.decimals ?? 2)}`}
                  </td>
                ))}
              </tr>
            ))}
            <tr style={{ borderTop: '2px solid var(--color-divider)', color: 'var(--color-neutral-600)' }}>
              <td>Median</td>
              {cols.map((c) => {
                const vals = peers.map((p) => p[c.key] as number | null).filter((v): v is number => v !== null)
                const m = median(vals)
                return (
                  <td key={c.key} style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {m === null ? '—' : `${c.prefix ?? ''}${fmtNum(m, c.decimals ?? 2)}`}
                  </td>
                )
              })}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

/** Mirrors the real layout rather than showing a spinner, so the page doesn't
 *  reflow under the user once the data lands. */
function StockDetailSkeleton() {
  return (
    <div style={{ maxWidth: 1100 }} aria-busy="true" aria-label="Loading stock">
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginBottom: 22 }}>
        <div className="skeleton" style={{ width: 120, height: 28 }} />
        <div className="skeleton" style={{ width: 220, height: 15 }} />
        <div style={{ flex: 1 }} />
        <div className="skeleton" style={{ width: 130, height: 28 }} />
      </div>
      <div className="detail-grid">
        <div className="skeleton" style={{ height: 460 }} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {[190, 150, 150].map((h, i) => <div key={i} className="skeleton" style={{ height: h }} />)}
        </div>
      </div>
    </div>
  )
}

export function StockDetailPage() {
  const { id } = useParams<{ id: string }>()
  const instrumentId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const back = location.state as { from?: string; fromLabel?: string } | null

  const { data: instrument, loading, error } = useFetch<InstrumentDetail>(`/api/instruments/${instrumentId}`, [instrumentId])
  usePageHeader(instrument?.symbol ?? '…', instrument?.company_name ?? null)

  const [study1, setStudy1] = useState(() => readStudyPref('chart-study-1'))
  const [study2, setStudy2] = useState(() => readStudyPref('chart-study-2'))
  function updateStudy1(v: string) {
    setStudy1(v)
    writeStudyPref('chart-study-1', v)
  }
  function updateStudy2(v: string) {
    setStudy2(v)
    writeStudyPref('chart-study-2', v)
  }

  const [chartMode, setChartMode] = useState<'native' | 'embed'>(() => (readStudyPref('chart-mode') === 'embed' ? 'embed' : 'native'))
  function updateChartMode(v: 'native' | 'embed') {
    setChartMode(v)
    writeStudyPref('chart-mode', v)
  }

  // The prices endpoint always orders ascending with no "latest first" option,
  // and there can be years of history -- so ask for just the last ~2 months
  // by date (comfortably more than 30 trading days) instead of paginating
  // from the start.
  const sinceDate = new Date()
  sinceDate.setDate(sinceDate.getDate() - 60)
  const since = sinceDate.toISOString().slice(0, 10)
  const { data: prices } = useFetch<Page<PriceOut>>(`/api/instruments/${instrumentId}/prices?from=${since}&limit=200`, [instrumentId])
  const recentPrices = prices ? [...prices.items].reverse().slice(0, 15) : []

  if (loading) return <StockDetailSkeleton />
  if (error) return <ErrorText>{error}</ErrorText>
  if (!instrument) return null

  const ind = instrument.latest_indicators
  const chg = changeVisual(instrument.day_change_pct)
  const rsi = ind?.rsi_14 ?? null
  const rsiFlag = rsi !== null && rsi >= 70
    ? { label: 'Overbought', bg: 'var(--color-warn-bg)', color: 'var(--color-warn-text)' }
    : rsi !== null && rsi <= 30
      ? { label: 'Oversold', bg: 'var(--color-warn-bg)', color: 'var(--color-warn-text)' }
      : null

  return (
    <div style={{ maxWidth: 1100 }}>
      {back?.from && (
        <button
          type="button"
          onClick={() => navigate(back.from!)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, background: 'none', border: 'none', color: 'var(--color-accent-700)', fontFamily: 'var(--font-body)', fontSize: 13, cursor: 'pointer', padding: '0 0 14px' }}
        >
          <IconArrowLeft />
          Back{back.fromLabel ? ` to ${back.fromLabel}` : ''}
        </button>
      )}

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap', marginBottom: 22 }}>
        <h2 style={{ margin: 0, fontSize: 25 }}>{instrument.symbol}</h2>
        <span style={{ color: 'var(--color-neutral-600)', fontSize: 15 }}>{instrument.company_name}</span>
        {instrument.sector && <span className="tag tag-neutral" style={{ whiteSpace: 'nowrap' }}>{instrument.sector}</span>}
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: 'var(--font-heading)', fontSize: 28, fontWeight: 600 }}>{fmtPrice(instrument.latest_close)}</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 15, fontWeight: 600, color: chg.color }}>
          <ChangeGlyph v={chg} />
          {fmtPct(instrument.day_change_pct)} ({fmtPrice(instrument.day_change_abs !== null ? Math.abs(instrument.day_change_abs) : null)})
        </span>
      </div>

      <div className="detail-grid">
        <div className="card" style={{ padding: '20px 22px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
            <div style={{ display: 'flex', background: 'var(--color-surface-2)', borderRadius: 999, padding: 4 }}>
              {(['native', 'embed'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  aria-pressed={chartMode === m}
                  onClick={() => updateChartMode(m)}
                  style={{
                    border: 'none', borderRadius: 999, fontSize: 13, fontWeight: 600, padding: '8px 16px', cursor: 'pointer',
                    fontFamily: 'var(--font-body)',
                    background: chartMode === m ? 'var(--color-brand)' : 'transparent',
                    color: chartMode === m ? '#fff' : 'var(--color-neutral-600)',
                  }}
                >
                  {m === 'native' ? 'Native chart' : 'TradingView'}
                </button>
              ))}
            </div>
          </div>

          {chartMode === 'native' ? (
            <NativeChart prices={prices?.items ?? []} />
          ) : (
            <>
              <div style={{ display: 'flex', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
                <StudyPicker label="Indicator 1" value={study1} onChange={updateStudy1} otherValue={study2} />
                <StudyPicker label="Indicator 2" value={study2} onChange={updateStudy2} otherValue={study1} />
              </div>
              <TradingViewChart symbol={instrument.tv_symbol} studies={[study1, study2].filter(Boolean)} />
            </>
          )}

          <h3 className="section-label" style={{ margin: '20px 0 8px' }}>Recent price history</h3>
          <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th>Date</th><th style={{ textAlign: 'right' }}>Open</th><th style={{ textAlign: 'right' }}>High</th><th style={{ textAlign: 'right' }}>Low</th><th style={{ textAlign: 'right' }}>Close</th><th style={{ textAlign: 'right' }}>Volume</th></tr>
            </thead>
            <tbody>
              {recentPrices.map((p) => (
                <tr key={p.trade_date}>
                  <td className="text-muted">{p.trade_date}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(p.open)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(p.high)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(p.low)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>{fmtNum(p.close)}</td>
                  <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{indianNum(p.volume, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <IndicatorCard
            kicker="Trend"
            rows={[
              { label: 'SMA 20', value: fmtNum(ind?.sma_20) }, { label: 'SMA 50', value: fmtNum(ind?.sma_50) },
              { label: 'SMA 100', value: fmtNum(ind?.sma_100) }, { label: 'SMA 200', value: fmtNum(ind?.sma_200) },
              { label: 'EMA 20', value: fmtNum(ind?.ema_20) }, { label: 'EMA 50', value: fmtNum(ind?.ema_50) },
            ]}
          />
          <IndicatorCard
            kicker="Momentum"
            rows={[
              { label: 'RSI 14', value: fmtNum(rsi), flag: rsiFlag },
              { label: 'MACD line', value: fmtNum(ind?.macd) },
              { label: 'MACD signal', value: fmtNum(ind?.macd_signal) },
              { label: 'MACD histogram', value: fmtNum(ind?.macd_histogram) },
            ]}
          />
          <IndicatorCard
            kicker="Volatility & range"
            rows={[
              { label: 'ATR 14', value: fmtNum(ind?.atr_14) },
              { label: '20D vol. average', value: indianNum(ind?.volume_sma_20, 0) },
              { label: '52W high', value: fmtPrice(ind?.high_52w) },
              { label: '52W low', value: fmtPrice(ind?.low_52w) },
            ]}
          />
          <CustomCrossoverCard instrumentId={instrumentId} />
          {ind && <p className="text-muted" style={{ fontSize: 11, margin: 0 }}>Indicators as of {ind.trade_date}</p>}
        </div>
      </div>

      <PeerComparisonCard instrumentId={instrumentId} />
    </div>
  )
}
