import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { BlueprintButton, DataTable, Field, type DataTableColumn } from '../components/ui'
import { apiFetch, ApiError } from '../lib/api'
import { fmtPrice } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { queryKeys } from '../lib/queryKeys'
import { useToast } from '../lib/toast'
import { isCrossoverInvalid } from '../lib/validation'
import type { MaType, Page, PortfolioReportItemOut, PortfolioReportOut, PortfolioReportSummary, ScanDirection, ScanResponse } from '../lib/types'

type ScanRequestBody = ScanResponse['params']

export function CustomScanPage() {
  usePageHeader('Custom Scan', 'Scan the whole market for a custom-period MA crossover — takes a few seconds, unlike the instant Screener')
  const toast = useToast()
  const queryClient = useQueryClient()

  const [fast, setFast] = useState('9')
  const [slow, setSlow] = useState('21')
  const [maType, setMaType] = useState<MaType>('ema')
  const [direction, setDirection] = useState<ScanDirection>('any')

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [activeReportId, setActiveReportId] = useState<number | null>(null)
  const [watchlistOnly, setWatchlistOnly] = useState(false)

  const { data: reportsPage } = useQuery({
    queryKey: queryKeys.portfolioReports.list(),
    queryFn: () => apiFetch<Page<PortfolioReportSummary>>('/api/portfolio-reports?limit=50'),
  })
  const reports = reportsPage?.items ?? []

  const { data: activeReport } = useQuery({
    queryKey: queryKeys.portfolioReports.detail(activeReportId ?? -1),
    queryFn: () => apiFetch<PortfolioReportOut>(`/api/portfolio-reports/${activeReportId}`),
    enabled: activeReportId !== null,
  })

  const invalid = isCrossoverInvalid(fast, slow)

  const scanMutation = useMutation({
    mutationFn: (body: ScanRequestBody) => apiFetch<ScanResponse>('/api/scans/crossover', { method: 'POST', body: JSON.stringify(body) }),
  })

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData()
      body.append('file', file)
      return apiFetch<PortfolioReportOut>('/api/portfolio-reports', { method: 'POST', body })
    },
    onSuccess: (report) => {
      // Seeds the detail cache directly from the upload response instead of
      // waiting on the `activeReportId` query below to re-fetch it --
      // selecting a just-uploaded report is instant.
      queryClient.setQueryData(queryKeys.portfolioReports.detail(report.id), report)
      queryClient.invalidateQueries({ queryKey: queryKeys.portfolioReports.list() })
      setActiveReportId(report.id)
      scanMutation.reset()
    },
  })

  const saveWatchlistMutation = useMutation({
    mutationFn: (reportId: number) => apiFetch<{ name: string }>(`/api/portfolio-reports/${reportId}/watchlist`, { method: 'POST' }),
    onSuccess: (watchlist) => {
      toast(`Saved as watchlist "${watchlist.name}"`)
      queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all })
    },
    onError: (err) => toast(err instanceof ApiError ? err.message : 'could not save watchlist'),
  })

  function runScan() {
    if (invalid || scanMutation.isPending) return
    scanMutation.mutate({
      fast: Number(fast),
      slow: Number(slow),
      ma_type: maType,
      direction,
      report_id: activeReportId,
      watchlist_only: watchlistOnly,
    })
  }

  function handleFileChosen(file: File) {
    uploadMutation.mutate(file, { onSettled: () => { if (fileInputRef.current) fileInputRef.current.value = '' } })
  }

  function selectReport(id: number | null) {
    setActiveReportId(id)
    scanMutation.reset()
  }

  const result = scanMutation.data
  const unmatched = activeReport?.items.filter((i) => !i.matched) ?? []
  const pdfByInstrument = new Map(
    activeReport?.items.filter((i): i is PortfolioReportItemOut & { instrument_id: number } => i.instrument_id !== null).map((i) => [i.instrument_id, i]),
  )

  const columns: DataTableColumn<ScanResponse['matches'][number]>[] = [
    {
      header: 'Symbol',
      render: (m) => <Link to={`/stocks/${m.instrument_id}`} state={{ from: '/scan', fromLabel: 'Custom Scan' }}><strong>{m.symbol}</strong></Link>,
    },
    { header: 'Sector', render: (m) => (m.sector ? <span className="tag tag-outline">{m.sector}</span> : <span className="text-muted">—</span>) },
    { header: 'Price', align: 'right', render: (m) => fmtPrice(m.latest_close) },
    {
      header: 'Signal',
      render: (m) => <span className="tag tag-accent">{m.signal === 'crossed_above' ? 'Crossed above' : 'Crossed below'}</span>,
    },
    ...(activeReport
      ? ([
          {
            header: 'Group',
            render: (m) => {
              const grp = pdfByInstrument.get(m.instrument_id)?.grp
              return grp ? <span className="tag tag-outline">{grp}</span> : <span className="text-muted">—</span>
            },
          },
          {
            header: 'Score',
            render: (m) => pdfByInstrument.get(m.instrument_id)?.score ?? <span className="text-muted">—</span>,
          },
          {
            header: 'Zone',
            render: (m) => {
              const zone = pdfByInstrument.get(m.instrument_id)?.zone
              return zone ? <span className="tag tag-neutral">Zone {zone}</span> : <span className="text-muted">—</span>
            },
          },
        ] satisfies DataTableColumn<ScanResponse['matches'][number]>[])
      : []),
  ]

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <Field label="Portfolio report">
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
            disabled={uploadMutation.isPending}
            style={{ whiteSpace: 'nowrap' }}
          >
            {uploadMutation.isPending ? 'Uploading…' : 'Upload PDF'}
          </button>
        </Field>
        {reports.length > 0 && (
          <Field label="Past uploads">
            <select
              className="input"
              style={{ width: 220 }}
              value={activeReportId ?? ''}
              onChange={(e) => selectReport(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">— whole market —</option>
              {reports.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.filename} ({r.matched_count}/{r.ticker_count})
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Watchlists" hideLabel>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, minHeight: 34 }}>
            <input type="checkbox" checked={watchlistOnly} onChange={(e) => setWatchlistOnly(e.target.checked)} />
            Only stocks in my watchlists
          </label>
        </Field>
      </div>

      {uploadMutation.isError && (
        <p style={{ fontSize: 13, color: 'var(--color-neg-text)', marginBottom: 10 }}>
          {uploadMutation.error instanceof ApiError ? uploadMutation.error.message : 'upload failed'}
        </p>
      )}

      {activeReport && (
        <div style={{ marginBottom: 14, fontSize: 12.5 }}>
          <p style={{ color: 'var(--color-neutral-600)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span>
              <strong>{activeReport.filename}</strong>
              {activeReport.report_date && ` · ${activeReport.report_date}`} · {activeReport.matched_count}/{activeReport.ticker_count} tickers matched
            </span>
            {activeReport.matched_count > 0 && (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => saveWatchlistMutation.mutate(activeReport.id)}
                disabled={saveWatchlistMutation.isPending}
                style={{ fontSize: 12 }}
              >
                {saveWatchlistMutation.isPending ? 'Saving…' : 'Save as watchlist'}
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
        <Field label="Fast period">
          <input className="input" style={{ width: 90 }} value={fast} onChange={(e) => setFast(e.target.value)} />
        </Field>
        <Field label="Slow period">
          <input className="input" style={{ width: 90 }} value={slow} onChange={(e) => setSlow(e.target.value)} />
        </Field>
        <Field label="Type">
          <select className="input" style={{ width: 90 }} value={maType} onChange={(e) => setMaType(e.target.value as MaType)}>
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </Field>
        <Field label="Direction">
          <select className="input" style={{ width: 150 }} value={direction} onChange={(e) => setDirection(e.target.value as ScanDirection)}>
            <option value="any">Both</option>
            <option value="crossed_above">Crossed above</option>
            <option value="crossed_below">Crossed below</option>
          </select>
        </Field>
        <BlueprintButton onClick={runScan} disabled={invalid || scanMutation.isPending} style={{ whiteSpace: 'nowrap' }}>
          {scanMutation.isPending ? 'Running…' : 'Run scan'}
        </BlueprintButton>
      </div>
      {invalid && <p className="text-muted" style={{ fontSize: 12, marginBottom: 14 }}>fast must be a positive integer less than slow (max 400).</p>}
      {scanMutation.isError && (
        <p style={{ fontSize: 13, color: 'var(--color-neg-text)', marginBottom: 14 }}>
          {scanMutation.error instanceof ApiError ? scanMutation.error.message : 'scan failed'}
        </p>
      )}

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
            <DataTable columns={columns} rows={result.matches} rowKey={(m) => m.instrument_id} />
          )}
        </>
      )}
    </div>
  )
}
