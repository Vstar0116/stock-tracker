import { useCallback, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Corners } from '../components/Blueprint'
import { EmptyState } from '../components/EmptyState'
import { apiFetch, ApiError } from '../lib/api'
import { ErrorText, fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { SortableTh, useSortableRows } from '../lib/sort'
import { useFetch } from '../lib/useFetch'
import {
  validateZoneParams, ZONE_PARAM_DEFAULTS, ZONE_PARAM_GROUPS, zoneFieldLabel,
  type ZoneParamKey, type ZoneParamsState,
} from '../lib/zoneParams'
import type { Page, ScanDirection, ScanResponse, WatchlistOut, Zone, ZoneProtocolParseResponse, ZoneScanResponse, ZoneOut } from '../lib/types'

const ZONE_COLORS: Record<Zone, string> = {
  A: 'var(--color-pos-text)',
  B: 'var(--color-pos-text)',
  C: 'var(--color-neg-text)',
  D: 'var(--color-neg-text)',
  Unclassified: 'var(--color-neutral-600)',
  'Insufficient Data': 'var(--color-neutral-600)',
}

export function CustomScanPage() {
  usePageHeader('Custom Scan', 'Whole-market MA crossover or zone classification. Takes a few seconds.')

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
  const [zoneWatchlistId, setZoneWatchlistId] = useState('')

  const { data: watchlistPage } = useFetch<Page<WatchlistOut>>(scanType === 'zone' ? '/api/watchlists?limit=200' : null, [scanType])
  const watchlists = watchlistPage?.items ?? []

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

  const zoneInvalid = useMemo(() => validateZoneParams(zoneParams), [zoneParams])

  async function runZoneScan() {
    if (zoneLoading || zoneInvalid) return
    setZoneLoading(true)
    setZoneError(null)
    try {
      const qs = new URLSearchParams(zoneParams as unknown as Record<string, string>)
      if (zoneWatchlistId) qs.set('watchlist_id', zoneWatchlistId)
      const res = await apiFetch<ZoneScanResponse>(`/api/zone/scan?${qs.toString()}`)
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
        <button type="button" className={scanType === 'crossover' ? 'btn btn-primary blueprint' : 'btn btn-secondary'} aria-pressed={scanType === 'crossover'} onClick={() => setScanType('crossover')}>
          {scanType === 'crossover' && <Corners />}
          MA Crossover
        </button>
        <button type="button" className={scanType === 'zone' ? 'btn btn-primary blueprint' : 'btn btn-secondary'} aria-pressed={scanType === 'zone'} onClick={() => setScanType('zone')}>
          {scanType === 'zone' && <Corners />}
          Zone Classifier
        </button>
      </div>

      {scanType === 'zone' ? (
        <ZoneScanSection
          params={zoneParams} onParamsChange={setZoneParams} invalid={zoneInvalid}
          watchlists={watchlists} watchlistId={zoneWatchlistId} onWatchlistIdChange={setZoneWatchlistId}
          result={zoneResult} loading={zoneLoading} error={zoneError} onRun={runZoneScan}
        />
      ) : (
      <div className="reveal">
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">Fast period</span>
          <input className="input" type="number" min={1} max={400} style={{ width: 90 }} value={fast} onChange={(e) => setFast(e.target.value)} />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">Slow period</span>
          <input className="input" type="number" min={2} max={400} style={{ width: 90 }} value={slow} onChange={(e) => setSlow(e.target.value)} />
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">Type</span>
          <select className="input" style={{ width: 90 }} value={maType} onChange={(e) => setMaType(e.target.value as 'sma' | 'ema')}>
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </label>
        <label className="field" style={{ margin: 0 }}>
          <span className="field-label">Direction</span>
          <select className="input" style={{ width: 150 }} value={direction} onChange={(e) => setDirection(e.target.value as ScanDirection)}>
            <option value="any">Both</option>
            <option value="crossed_above">Crossed above</option>
            <option value="crossed_below">Crossed below</option>
          </select>
        </label>
        <button type="button" className="btn btn-primary blueprint" onClick={runScan} disabled={invalid || loading} style={{ whiteSpace: 'nowrap' }}>
          <Corners />
          {loading ? 'Running…' : 'Run scan'}
        </button>
      </div>
      {invalid && <ErrorText style={{ fontSize: 12 }}>Fast must be a whole number below slow, and slow at most 400.</ErrorText>}
      {error && <ErrorText>{error}</ErrorText>}

      {result && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginBottom: 12 }}>
            As of {result.as_of} — {result.stats.matched} of {result.stats.evaluated} evaluated
            {result.stats.skipped_stale > 0 && `, ${result.stats.skipped_stale} stale`}
            {result.stats.skipped_insufficient_history > 0 && `, ${result.stats.skipped_insufficient_history} short on history`}
            {' — '}{result.stats.elapsed_ms}ms{result.stats.cached ? ' (cached)' : ''}
          </p>
          {result.matches.length === 0 ? (
            <EmptyState
              title="No stocks currently match this crossover."
              hint="Widen the gap between the fast and slow periods, or set Direction to Both."
            />
          ) : (
            <CrossoverTable matches={result.matches} />
          )}
        </>
      )}
      </div>
      )}
    </div>
  )
}

type CrossoverMatch = ScanResponse['matches'][number]

const crossoverSortValue = (m: CrossoverMatch, key: string): unknown => {
  if (key === 'latest_close') return m.latest_close
  return m[key as 'symbol' | 'sector' | 'signal']
}

function CrossoverTable({ matches }: { matches: CrossoverMatch[] }) {
  const { rows, sort, toggle } = useSortableRows(matches, crossoverSortValue)
  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <SortableTh label="Symbol" sortKey="symbol" sort={sort} onSort={toggle} />
            <SortableTh label="Sector" sortKey="sector" sort={sort} onSort={toggle} />
            <SortableTh label="Price" sortKey="latest_close" sort={sort} onSort={toggle} numeric />
            <SortableTh label="Signal" sortKey="signal" sort={sort} onSort={toggle} />
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
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
    </div>
  )
}

const zoneSortValue = (m: ZoneOut, key: string): unknown => m[key as keyof ZoneOut]
// Stable identity so the sort memo doesn't re-run on every render before a scan.
const EMPTY_ZONE_ROWS: ZoneOut[] = []

function ZoneScanSection({
  params, onParamsChange, invalid, watchlists, watchlistId, onWatchlistIdChange, result, loading, error, onRun,
}: {
  params: ZoneParamsState
  onParamsChange: (p: ZoneParamsState) => void
  invalid: string | null
  watchlists: WatchlistOut[]
  watchlistId: string
  onWatchlistIdChange: (id: string) => void
  result: ZoneScanResponse | null
  loading: boolean
  error: string | null
  onRun: () => void
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [pdfBusy, setPdfBusy] = useState(false)
  const [pdfMessage, setPdfMessage] = useState<string | null>(null)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { rows: zoneRows, sort, toggle } = useSortableRows(result?.matches ?? EMPTY_ZONE_ROWS, zoneSortValue)

  function setField(key: ZoneParamKey, value: string) {
    onParamsChange({ ...params, [key]: value })
  }

  const fieldLabel = useCallback((key: string) => zoneFieldLabel(key), [])

  async function uploadProtocolPdf(file: File) {
    setPdfBusy(true)
    setPdfError(null)
    setPdfMessage(null)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await apiFetch<ZoneProtocolParseResponse>('/api/zone/parse-protocol', { method: 'POST', body: formData })
      const foundEntries = Object.entries(res.found) as [ZoneParamKey, number][]
      if (foundEntries.length > 0) {
        const next = { ...params }
        for (const [key, value] of foundEntries) next[key] = String(value)
        onParamsChange(next)
        setShowAdvanced(true)
      }
      setPdfMessage(
        foundEntries.length === 0
          ? 'No recognizable parameters found in this PDF -- nothing changed.'
          : `Applied from PDF: ${foundEntries.map(([key]) => fieldLabel(key)).join(', ')}.`
            + (res.not_found.length > 0 ? ` Not found, left unchanged: ${res.not_found.map(fieldLabel).join(', ')}.` : '')
      )
    } catch (err) {
      setPdfError(err instanceof ApiError ? err.message : 'could not read PDF')
    } finally {
      setPdfBusy(false)
    }
  }

  return (
    <div className="reveal">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-primary blueprint" onClick={onRun} disabled={loading || invalid !== null} style={{ whiteSpace: 'nowrap' }}>
          <Corners />
          {loading ? 'Running…' : 'Run scan'}
        </button>
        <div className="field" style={{ margin: 0 }}>
          <select
            className="input" style={{ width: 190 }}
            value={watchlistId} onChange={(e) => onWatchlistIdChange(e.target.value)}
            aria-label="Scan scope"
          >
            <option value="">Whole market</option>
            {watchlists.map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        </div>
        <input
          ref={fileInputRef} type="file" accept="application/pdf" style={{ display: 'none' }}
          onChange={(e) => {
            const file = e.target.files?.[0]
            e.target.value = ''
            if (file) uploadProtocolPdf(file)
          }}
        />
        <button
          type="button" className="btn btn-secondary" style={{ fontSize: 12.5 }}
          onClick={() => fileInputRef.current?.click()} disabled={pdfBusy}
        >
          {pdfBusy ? 'Reading PDF…' : 'Upload protocol PDF'}
        </button>
        <button
          type="button" className="btn btn-ghost" style={{ fontSize: 12.5, padding: 0, marginLeft: 'auto' }}
          onClick={() => setShowAdvanced((s) => !s)} aria-expanded={showAdvanced}
        >
          {showAdvanced ? 'Hide' : 'Show'} advanced parameters
        </button>
      </div>

      {/* The zone letters were explained only by a title tooltip, which is
          invisible on touch and to a screen reader. */}
      <p className="text-muted" style={{ fontSize: 12.5, margin: '0 0 14px', maxWidth: 720 }}>
        Every stock is placed in a zone by where its RSI sits relative to its trend.
        <strong> A</strong> and <strong>B</strong> are constructive (RSI up to the Zone B band, price holding its EMAs);
        <strong> C</strong> and <strong>D</strong> are extended (RSI in the upper bands). Thresholds are yours to set below.
      </p>

      {pdfError && <ErrorText>{pdfError}</ErrorText>}
      {pdfMessage && <p role="status" style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginBottom: 14 }}>{pdfMessage}</p>}

      {showAdvanced && (
        <div className="card blueprint reveal" style={{ padding: 16, marginBottom: 18 }}>
          <Corners />
          {ZONE_PARAM_GROUPS.map((group) => (
            <div key={group.title} style={{ marginBottom: 14 }}>
              <div className="card-kicker" style={{ marginBottom: 8 }}>{group.title}</div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {group.fields.map((f) => (
                  <label key={f.key} className="field" style={{ margin: 0 }}>
                    <span className="field-label">{f.label}</span>
                    <input
                      className="input" style={{ width: f.width ?? 100 }}
                      type="number" min={f.min} max={f.max} step={f.step ?? (f.int ? '1' : 'any')}
                      value={params[f.key]} onChange={(e) => setField(f.key, e.target.value)}
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
          <button
            type="button" className="btn btn-secondary" style={{ fontSize: 12.5, alignSelf: 'flex-start' }}
            onClick={() => onParamsChange(ZONE_PARAM_DEFAULTS)}
          >
            Reset to defaults
          </button>
        </div>
      )}
      {/* Caught here rather than as a 422 from the server, matching what the
          Crossover tab on this same page already did. */}
      {invalid && <ErrorText>{invalid}</ErrorText>}
      {error && <ErrorText>{error}</ErrorText>}

      {result && (
        <>
          <p style={{ fontSize: 12.5, color: 'var(--color-neutral-600)', marginBottom: 12 }}>
            As of {result.as_of} — {result.matches.length} of {result.evaluated} evaluated
            {result.skipped.length > 0 && `, ${result.skipped.length} skipped`}
            {' — '}{result.elapsed_ms}ms{result.cached ? ' (cached)' : ''}
          </p>
          {result.matches.length === 0 ? (
            <EmptyState
              title="No stocks currently classify into a zone."
              hint="Widen the RSI bands under advanced parameters, or scan the whole market instead of a single watchlist."
            />
          ) : (
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <SortableTh label="Symbol" sortKey="ticker" sort={sort} onSort={toggle} />
                    <SortableTh label="Zone" sortKey="zone" sort={sort} onSort={toggle} />
                    <SortableTh label="RSI" sortKey="rsi" sort={sort} onSort={toggle} numeric />
                    <SortableTh label="Price" sortKey="price" sort={sort} onSort={toggle} numeric />
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {zoneRows.map((m) => (
                    <tr key={m.instrument_id}>
                      <td><Link to={`/stocks/${m.instrument_id}`} state={{ from: '/scan', fromLabel: 'Custom Scan' }}><strong>{m.ticker}</strong></Link></td>
                      <td>
                        <span className="tag tag-outline" style={{ color: ZONE_COLORS[m.zone], borderColor: ZONE_COLORS[m.zone], whiteSpace: 'nowrap' }}>
                          {m.zone}
                          <span className="sr-only"> — {m.zone_label}</span>
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{m.rsi !== null ? m.rsi.toFixed(1) : '—'}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.price)}</td>
                      <td style={{ fontSize: 12.5, color: 'var(--color-neutral-600)' }}>{m.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
