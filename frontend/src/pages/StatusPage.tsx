import { fmtNum } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { useFetch } from '../lib/useFetch'
import type { StatusDetailOut } from '../lib/types'

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 0) return 'just now'
  const mins = Math.floor(ms / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m ago`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h ago`
}

function duration(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
}

function FreshnessCard({ status }: { status: StatusDetailOut }) {
  const ok = status.is_current
  const border = ok ? 'var(--color-pos-border)' : 'var(--color-warn-border)'
  const bg = ok ? 'var(--color-pos-bg)' : 'var(--color-warn-bg)'
  const text = ok ? 'var(--color-pos-text)' : 'var(--color-warn-text)'
  return (
    <div style={{ border: `1px solid ${border}`, background: bg, color: text, padding: '16px 18px', marginBottom: 20 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
        {ok ? 'Data is current' : 'Data is stale'}
      </div>
      <div style={{ fontSize: 13 }}>
        Latest trade date: {status.latest_trade_date ?? 'no data'} (expected {status.expected_trade_date})
      </div>
    </div>
  )
}

export function StatusPage() {
  usePageHeader('System Status', 'Admin-only: pipeline health, data freshness, and job history')
  const { data: status, loading, error } = useFetch<StatusDetailOut>('/api/status/detail')

  if (loading) return <p>Loading…</p>
  if (error) return <p style={{ color: 'var(--color-neg-text)' }}>{error}</p>
  if (!status) return null

  return (
    <div style={{ maxWidth: 900 }}>
      <FreshnessCard status={status} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 24 }}>
        <div className="card">
          <div className="card-kicker">Last successful pipeline run</div>
          <div className="card-title" style={{ fontSize: 15 }}>{ago(status.last_successful_pipeline_run_at)}</div>
          <p className="card-body">{status.last_successful_pipeline_run_at ?? 'no successful run recorded'}</p>
        </div>
        <div className="card">
          <div className="card-kicker">Most recent run status</div>
          <div className="card-title" style={{ fontSize: 15 }}>{status.last_pipeline_status ?? 'never run'}</div>
          <p className="card-body">{ago(status.last_pipeline_run_at)}</p>
        </div>
        <div className="card">
          <div className="card-kicker">Instruments</div>
          <div className="card-title" style={{ fontSize: 15 }}>{fmtNum(status.instrument_count, 0)}</div>
        </div>
        <div className="card">
          <div className="card-kicker">Daily prices</div>
          <div className="card-title" style={{ fontSize: 15 }}>{fmtNum(status.daily_price_count, 0)}</div>
        </div>
        <div className="card">
          <div className="card-kicker">Indicators</div>
          <div className="card-title" style={{ fontSize: 15 }}>{fmtNum(status.indicator_count, 0)}</div>
        </div>
        <div className="card">
          <div className="card-kicker">Natural-language screening</div>
          <div className="card-title" style={{ fontSize: 15 }}>
            {!status.nl_screen_configured ? 'Not configured' : status.nl_screen_reachable ? 'Reachable' : 'Unreachable'}
          </div>
          <p className="card-body">
            {!status.nl_screen_configured
              ? 'GROQ_API_KEY is unset'
              : status.nl_screen_reachable
                ? 'Groq API responded'
                : 'Configured but the Groq API did not respond'}
          </p>
        </div>
      </div>

      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 600, fontSize: 13, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--color-neutral-600)', marginBottom: 8 }}>
        Last 10 job runs
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Job</th>
            <th>Status</th>
            <th>Started</th>
            <th>Duration</th>
            <th>Rows</th>
          </tr>
        </thead>
        <tbody>
          {status.recent_job_runs.map((r, i) => (
            <tr key={i}>
              <td>{r.job_name}</td>
              <td>
                <span className="tag" style={{ background: r.status === 'success' ? 'var(--color-pos-bg)' : r.status === 'failed' ? 'var(--color-neg-bg)' : 'var(--color-neutral-100)', color: r.status === 'success' ? 'var(--color-pos-text)' : r.status === 'failed' ? 'var(--color-neg-text)' : 'var(--color-neutral-800)' }}>
                  {r.status}
                </span>
              </td>
              <td>{new Date(r.started_at).toLocaleString('en-IN')}</td>
              <td>{duration(r.duration_seconds)}</td>
              <td>{r.rows_processed ?? '—'}</td>
            </tr>
          ))}
          {status.recent_job_runs.length === 0 && (
            <tr>
              <td colSpan={5} style={{ color: 'var(--color-neutral-600)' }}>No job runs recorded yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
