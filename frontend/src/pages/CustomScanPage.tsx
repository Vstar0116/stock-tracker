import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, ApiError } from '../lib/api'
import { fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import type { ScanDirection, ScanResponse } from '../lib/types'

export function CustomScanPage() {
  usePageHeader('Custom Scan', 'Scan the whole market for a custom-period MA crossover — takes a few seconds, unlike the instant Screener')

  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<'sma' | 'ema'>('ema')
  const [direction, setDirection] = useState<ScanDirection>('any')
  const [result, setResult] = useState<ScanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  return (
    <div style={{ maxWidth: 900 }}>
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
    </div>
  )
}
