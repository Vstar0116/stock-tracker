import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, ApiError } from '../lib/api'
import { fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import type { ScanDirection, ScanResponse, Zone, ZoneScanResponse } from '../lib/types'

const ZONE_COLORS: Record<Zone, string> = {
  A: 'var(--color-pos-text)',
  B: 'var(--color-pos-text)',
  C: 'var(--color-neg-text)',
  D: 'var(--color-neg-text)',
  Unclassified: 'var(--color-neutral-600)',
  'Insufficient Data': 'var(--color-neutral-600)',
}

// Mirrors app/api/zone.py's _params_from_query defaults.
const ZONE_PARAM_DEFAULTS = {
  macro_sma_period: '200',
  fast_ema_period: '9',
  slow_ema_period: '21',
  rsi_period: '14',
  rsi_zone_a_max: '55',
  rsi_zone_b_low: '56',
  rsi_zone_b_high: '65',
  rsi_zone_c_low: '66',
  rsi_zone_c_high: '71',
  rsi_zone_d_min: '72',
  atr_period: '14',
  atr_limit_multiplier: '0.25',
  rvol_period: '20',
  near_ema_pct: '0.02',
}

type ZoneParamsState = typeof ZONE_PARAM_DEFAULTS
type ZoneParamKey = keyof ZoneParamsState

export function CustomScanPage() {
  usePageHeader('Custom Scan', 'Scan the whole market — MA crossover or BS-V4 zone classification — takes a few seconds, unlike the instant Screener')

  const [scanType, setScanType] = useState<'crossover' | 'zone'>('crossover')

  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<'sma' | 'ema'>('ema')
  const [direction, setDirection] = useState<ScanDirection>('any')
  const [result, setResult] = useState<ScanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [zoneParams, setZoneParams] = useState<ZoneParamsState>(ZONE_PARAM_DEFAULTS)
  const [zoneResult, setZoneResult] = useState<ZoneScanResponse | null>(null)
  const [zoneLoading, setZoneLoading] = useState(false)
  const [zoneError, setZoneError] = useState<string | null>(null)

  const fastNum = Number(fast)
  const slowNum = Number(slow)
  const invalid = !Number.isInteger(fastNum) || !Number.isInteger(slowNum) || fastNum < 1 || fastNum >= slowNum || slowNum > 400

  async function runScan() {
    if (invalid || loading) return
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch<ScanResponse>('/api/scans/crossover', {
        method: 'POST',
        body: JSON.stringify({ fast: fastNum, slow: slowNum, ma_type: maType, direction }),
      })
      setResult(res)
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err.message : 'scan failed')
    } finally {
      setLoading(false)
    }
  }

  async function runZoneScan() {
    if (zoneLoading) return
    setZoneLoading(true)
    setZoneError(null)
    try {
      const qs = new URLSearchParams(zoneParams as unknown as Record<string, string>).toString()
      const res = await apiFetch<ZoneScanResponse>(`/api/zone/scan?${qs}`)
      setZoneResult(res)
    } catch (err) {
      setZoneResult(null)
      setZoneError(err instanceof ApiError ? err.message : 'scan failed')
    } finally {
      setZoneLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
        <button type="button" className={scanType === 'crossover' ? 'btn btn-primary blueprint' : 'btn btn-secondary'} onClick={() => setScanType('crossover')}>
          {scanType === 'crossover' && (<><i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" /></>)}
          MA Crossover
        </button>
        <button type="button" className={scanType === 'zone' ? 'btn btn-primary blueprint' : 'btn btn-secondary'} onClick={() => setScanType('zone')}>
          {scanType === 'zone' && (<><i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" /></>)}
          Zone Classifier
        </button>
      </div>

      {scanType === 'zone' ? (
        <ZoneScanSection
          params={zoneParams} onParamsChange={setZoneParams}
          result={zoneResult} loading={zoneLoading} error={zoneError} onRun={runZoneScan}
        />
      ) : (
      <>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <div className="field" style={{ margin: 0 }}>
          <label>Fast period</label>
          <input className="input" style={{ width: 90 }} value={fast} onChange={(e) => setFast(e.target.value)} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>Slow period</label>
          <input className="input" style={{ width: 90 }} value={slow} onChange={(e) => setSlow(e.target.value)} />
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>Type</label>
          <select className="input" style={{ width: 90 }} value={maType} onChange={(e) => setMaType(e.target.value as 'sma' | 'ema')}>
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </div>
        <div className="field" style={{ margin: 0 }}>
          <label>Direction</label>
          <select className="input" style={{ width: 150 }} value={direction} onChange={(e) => setDirection(e.target.value as ScanDirection)}>
            <option value="any">Both</option>
            <option value="crossed_above">Crossed above</option>
            <option value="crossed_below">Crossed below</option>
          </select>
        </div>
        <button type="button" className="btn btn-primary blueprint" onClick={runScan} disabled={invalid || loading} style={{ whiteSpace: 'nowrap' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {loading ? 'Running…' : 'Run scan'}
        </button>
      </div>
      {invalid && <p className="text-muted" style={{ fontSize: 12, marginBottom: 14 }}>fast must be a positive integer less than slow (max 400).</p>}
      {error && <p style={{ fontSize: 13, color: 'var(--color-neg-text)', marginBottom: 14 }}>{error}</p>}

      {result && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginBottom: 12 }}>
            As of {result.as_of} — {result.stats.matched} of {result.stats.evaluated} evaluated
            {result.stats.skipped_stale > 0 && `, ${result.stats.skipped_stale} stale`}
            {result.stats.skipped_insufficient_history > 0 && `, ${result.stats.skipped_insufficient_history} short on history`}
            {' — '}{result.stats.elapsed_ms}ms{result.stats.cached ? ' (cached)' : ''}
          </p>
          {result.matches.length === 0 ? (
            <div style={{ padding: 26, textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 13, border: '1px solid var(--color-neutral-300)' }}>
              No stocks currently match this crossover.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Symbol</th><th>Sector</th><th style={{ textAlign: 'right' }}>Price</th><th>Signal</th></tr>
              </thead>
              <tbody>
                {result.matches.map((m) => (
                  <tr key={m.instrument_id}>
                    <td><Link to={`/stocks/${m.instrument_id}`} state={{ from: '/scan', fromLabel: 'Custom Scan' }}><strong>{m.symbol}</strong></Link></td>
                    <td>{m.sector ? <span className="tag tag-outline">{m.sector}</span> : <span className="text-muted">—</span>}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.latest_close)}</td>
                    <td>
                      <span className="tag tag-accent">{m.signal === 'crossed_above' ? 'Crossed above' : 'Crossed below'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      </>
      )}
    </div>
  )
}

const ZONE_PARAM_GROUPS: { title: string; fields: { key: ZoneParamKey; label: string; width?: number }[] }[] = [
  {
    title: 'Moving averages', fields: [
      { key: 'macro_sma_period', label: 'Macro SMA period' },
      { key: 'fast_ema_period', label: 'Fast EMA period' },
      { key: 'slow_ema_period', label: 'Slow EMA period' },
      { key: 'near_ema_pct', label: 'Near-EMA (fraction)' },
    ],
  },
  {
    title: 'RSI zones', fields: [
      { key: 'rsi_period', label: 'RSI period' },
      { key: 'rsi_zone_a_max', label: 'Zone A max' },
      { key: 'rsi_zone_b_low', label: 'Zone B low' },
      { key: 'rsi_zone_b_high', label: 'Zone B high' },
      { key: 'rsi_zone_c_low', label: 'Zone C low' },
      { key: 'rsi_zone_c_high', label: 'Zone C high' },
      { key: 'rsi_zone_d_min', label: 'Zone D min' },
    ],
  },
  {
    title: 'ATR / volume', fields: [
      { key: 'atr_period', label: 'ATR period' },
      { key: 'atr_limit_multiplier', label: 'ATR limit multiplier' },
      { key: 'rvol_period', label: 'RVol period' },
    ],
  },
]

function ZoneScanSection({
  params, onParamsChange, result, loading, error, onRun,
}: {
  params: ZoneParamsState
  onParamsChange: (p: ZoneParamsState) => void
  result: ZoneScanResponse | null
  loading: boolean
  error: string | null
  onRun: () => void
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)

  function setField(key: ZoneParamKey, value: string) {
    onParamsChange({ ...params, [key]: value })
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-primary blueprint" onClick={onRun} disabled={loading} style={{ whiteSpace: 'nowrap' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {loading ? 'Running…' : 'Run scan'}
        </button>
        <span className="text-muted" style={{ fontSize: 12.5 }}>Classifies every stock into Zone A–D by RSI/trend position.</span>
        <button type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0, marginLeft: 'auto' }} onClick={() => setShowAdvanced((s) => !s)}>
          {showAdvanced ? 'Hide' : 'Show'} advanced parameters
        </button>
      </div>

      {showAdvanced && (
        <div className="card blueprint" style={{ padding: 16, marginBottom: 18 }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {ZONE_PARAM_GROUPS.map((group) => (
            <div key={group.title} style={{ marginBottom: 14 }}>
              <div className="card-kicker" style={{ marginBottom: 8 }}>{group.title}</div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {group.fields.map((f) => (
                  <div key={f.key} className="field" style={{ margin: 0 }}>
                    <label>{f.label}</label>
                    <input
                      className="input" style={{ width: f.width ?? 100 }}
                      value={params[f.key]} onChange={(e) => setField(f.key, e.target.value)}
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
          <button
            type="button" className="btn btn-secondary" style={{ fontSize: 12.5 }}
            onClick={() => onParamsChange(ZONE_PARAM_DEFAULTS)}
          >
            Reset to defaults
          </button>
        </div>
      )}
      {error && <p style={{ fontSize: 13, color: 'var(--color-neg-text)', marginBottom: 14 }}>{error}</p>}

      {result && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginBottom: 12 }}>
            As of {result.as_of} — {result.matches.length} of {result.evaluated} evaluated
            {result.skipped.length > 0 && `, ${result.skipped.length} skipped`}
            {' — '}{result.elapsed_ms}ms{result.cached ? ' (cached)' : ''}
          </p>
          {result.matches.length === 0 ? (
            <div style={{ padding: 26, textAlign: 'center', color: 'var(--color-neutral-600)', fontSize: 13, border: '1px solid var(--color-neutral-300)' }}>
              No stocks currently classify into a zone.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr><th>Symbol</th><th>Zone</th><th style={{ textAlign: 'right' }}>RSI</th><th style={{ textAlign: 'right' }}>Price</th><th>Reason</th></tr>
              </thead>
              <tbody>
                {result.matches.map((m) => (
                  <tr key={m.instrument_id}>
                    <td><Link to={`/stocks/${m.instrument_id}`} state={{ from: '/scan', fromLabel: 'Custom Scan' }}><strong>{m.ticker}</strong></Link></td>
                    <td>
                      <span className="tag tag-outline" style={{ color: ZONE_COLORS[m.zone], borderColor: ZONE_COLORS[m.zone] }} title={m.zone_label}>
                        {m.zone}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{m.rsi !== null ? m.rsi.toFixed(1) : '—'}</td>
                    <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.price)}</td>
                    <td style={{ fontSize: 12.5, color: 'var(--color-neutral-600)' }}>{m.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  )
}
