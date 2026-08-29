import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch, ApiError } from '../lib/api'
import { fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { useToast } from '../lib/toast'
import { useFetch } from '../lib/useFetch'
import type { Page, PortfolioReportOut, PortfolioReportSummary, ScanDirection, ScanResponse } from '../lib/types'

export function CustomScanPage() {
  usePageHeader('Custom Scan', 'Scan the whole market for a custom-period MA crossover — takes a few seconds, unlike the instant Screener')
  const toast = useToast()

  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<'sma' | 'ema'>('ema')
  const [direction, setDirection] = useState<ScanDirection>('any')
  const [result, setResult] = useState<ScanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [activeReport, setActiveReport] = useState<PortfolioReportOut | null>(null)
  const [watchlistOnly, setWatchlistOnly] = useState(false)
  const [savingWatchlist, setSavingWatchlist] = useState(false)
  const { data: reportsPage, reload: reloadReports } = useFetch<Page<PortfolioReportSummary>>('/api/portfolio-reports?limit=50')
  const reports = reportsPage?.items ?? []

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
        body: JSON.stringify({
          fast: fastNum,
          slow: slowNum,
          ma_type: maType,
          direction,
          report_id: activeReport?.id ?? null,
          watchlist_only: watchlistOnly,
        }),
      })
      setResult(res)
    } catch (err) {
      setResult(null)
      setError(err instanceof ApiError ? err.message : 'scan failed')
    } finally {
      setLoading(false)
    }
  }

  async function handleFileChosen(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      const report = await apiFetch<PortfolioReportOut>('/api/portfolio-reports', { method: 'POST', body })
      setActiveReport(report)
      setResult(null)
      reloadReports()
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : 'upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function selectPastReport(id: number) {
    setUploadError(null)
    try {
      const report = await apiFetch<PortfolioReportOut>(`/api/portfolio-reports/${id}`)
      setActiveReport(report)
      setResult(null)
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : 'could not load report')
    }
  }

  async function saveAsWatchlist() {
    if (!activeReport || savingWatchlist) return
    setSavingWatchlist(true)
    try {
      const watchlist = await apiFetch<{ name: string }>(`/api/portfolio-reports/${activeReport.id}/watchlist`, { method: 'POST' })
      toast(`Saved as watchlist "${watchlist.name}"`)
    } catch (err) {
      toast(err instanceof ApiError ? err.message : 'could not save watchlist')
    } finally {
      setSavingWatchlist(false)
    }
  }

  const unmatched = activeReport?.items.filter((i) => !i.matched) ?? []
  const pdfByInstrument = new Map(activeReport?.items.filter((i) => i.instrument_id !== null).map((i) => [i.instrument_id as number, i]))

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <div className="field" style={{ margin: 0 }}>
          <label>Portfolio report</label>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleFileChosen(file)
            }}
          />
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            style={{ whiteSpace: 'nowrap' }}
          >
            {uploading ? 'Uploading…' : 'Upload PDF'}
          </button>
        </div>
        {reports.length > 0 && (
          <div className="field" style={{ margin: 0 }}>
            <label>Past uploads</label>
            <select
              className="input"
              style={{ width: 220 }}
              value={activeReport?.id ?? ''}
              onChange={(e) => (e.target.value ? selectPastReport(Number(e.target.value)) : setActiveReport(null))}
            >
              <option value="">— whole market —</option>
              {reports.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.filename} ({r.matched_count}/{r.ticker_count})
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="field" style={{ margin: 0 }}>
          <label style={{ visibility: 'hidden' }}>Watchlists</label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, minHeight: 34 }}>
            <input type="checkbox" checked={watchlistOnly} onChange={(e) => setWatchlistOnly(e.target.checked)} />
            Only stocks in my watchlists
          </label>
        </div>
      </div>

      {uploadError && <p style={{ fontSize: 13, color: 'var(--color-neg-text)', marginBottom: 10 }}>{uploadError}</p>}

      {activeReport && (
        <div style={{ marginBottom: 14, fontSize: 12.5 }}>
          <p style={{ color: 'var(--color-neutral-600)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>
              <strong>{activeReport.filename}</strong>
              {activeReport.report_date && ` · ${activeReport.report_date}`} · {activeReport.matched_count}/{activeReport.ticker_count} tickers matched
            </span>
            {activeReport.matched_count > 0 && (
              <button type="button" className="btn btn-ghost" onClick={saveAsWatchlist} disabled={savingWatchlist} style={{ fontSize: 12 }}>
                {savingWatchlist ? 'Saving…' : 'Save as watchlist'}
              </button>
            )}
          </p>
          {unmatched.length > 0 && (
            <p className="text-muted" style={{ marginTop: 2 }}>
              Not tracked: {unmatched.map((i) => i.ticker).join(', ')}
            </p>
          )}
        </div>
      )}

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
            As of {result.as_of} — {result.stats.matched} of {result.stats.universe ?? result.stats.evaluated} {result.stats.universe !== null ? 'in report' : 'evaluated'}
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
                <tr>
                  <th>Symbol</th>
                  <th>Sector</th>
                  <th style={{ textAlign: 'right' }}>Price</th>
                  <th>Signal</th>
                  {activeReport && (
                    <>
                      <th>Group</th>
                      <th>Score</th>
                      <th>Zone</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {result.matches.map((m) => {
                  const pdf = pdfByInstrument.get(m.instrument_id)
                  return (
                    <tr key={m.instrument_id}>
                      <td><Link to={`/stocks/${m.instrument_id}`} state={{ from: '/scan', fromLabel: 'Custom Scan' }}><strong>{m.symbol}</strong></Link></td>
                      <td>{m.sector ? <span className="tag tag-outline">{m.sector}</span> : <span className="text-muted">—</span>}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.latest_close)}</td>
                      <td>
                        <span className="tag tag-accent">{m.signal === 'crossed_above' ? 'Crossed above' : 'Crossed below'}</span>
                      </td>
                      {activeReport && (
                        <>
                          <td>{pdf?.grp ? <span className="tag tag-outline">{pdf.grp}</span> : <span className="text-muted">—</span>}</td>
                          <td>{pdf?.score ?? <span className="text-muted">—</span>}</td>
                          <td>{pdf?.zone ? <span className="tag tag-neutral">Zone {pdf.zone}</span> : <span className="text-muted">—</span>}</td>
                        </>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
