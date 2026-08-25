import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { TradingViewChart } from '../components/TradingViewChart'
import { changeVisual, ChangeGlyph, fmtNum, fmtPct, fmtPrice, indianNum } from '../lib/format'
import { usePageHeader } from '../lib/pageHeader'
import { useFetch } from '../lib/useFetch'
import type { InstrumentDetail, Page, PriceOut } from '../lib/types'

interface IndicatorRow {
  label: string
  value: string
  flag?: { label: string; bg: string; color: string } | null
}

function IndicatorCard({ kicker, rows }: { kicker: string; rows: IndicatorRow[] }) {
  return (
    <div className="card blueprint" style={{ padding: 16 }}>
      <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
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

export function StockDetailPage() {
  const { id } = useParams<{ id: string }>()
  const instrumentId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const back = location.state as { from?: string; fromLabel?: string } | null

  const { data: instrument, loading, error } = useFetch<InstrumentDetail>(`/api/instruments/${instrumentId}`, [instrumentId])
  usePageHeader(instrument?.symbol ?? '…', instrument?.company_name ?? null)

  // The prices endpoint always orders ascending with no "latest first" option,
  // and there can be years of history -- so ask for just the last ~2 months
  // by date (comfortably more than 30 trading days) instead of paginating
  // from the start.
  const sinceDate = new Date()
  sinceDate.setDate(sinceDate.getDate() - 60)
  const since = sinceDate.toISOString().slice(0, 10)
  const { data: prices } = useFetch<Page<PriceOut>>(`/api/instruments/${instrumentId}/prices?from=${since}&limit=200`, [instrumentId])
  const recentPrices = prices ? [...prices.items].reverse().slice(0, 15) : []

  if (loading) return <p className="text-muted" style={{ fontSize: 13 }}>Loading…</p>
  if (error) return <p style={{ fontSize: 13, color: 'var(--color-neg-text)' }}>{error}</p>
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
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" /><polyline points="12 19 5 12 12 5" />
          </svg>
          Back{back.fromLabel ? ` to ${back.fromLabel}` : ''}
        </button>
      )}

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap', marginBottom: 22 }}>
        <h3 style={{ margin: 0 }}>{instrument.symbol}</h3>
        <span style={{ color: 'var(--color-neutral-600)', fontSize: 15 }}>{instrument.company_name}</span>
        {instrument.sector && <span className="tag tag-outline" style={{ whiteSpace: 'nowrap' }}>{instrument.sector}</span>}
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: 'var(--font-heading)', fontSize: 28, fontWeight: 600 }}>{fmtPrice(instrument.latest_close)}</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 15, fontWeight: 600, color: chg.color }}>
          <ChangeGlyph v={chg} />
          {fmtPct(instrument.day_change_pct)} ({fmtPrice(instrument.day_change_abs !== null ? Math.abs(instrument.day_change_abs) : null)})
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 20, alignItems: 'start' }}>
        <div className="card blueprint" style={{ padding: 16 }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          <TradingViewChart
            symbol={
              instrument.exchange === 'BSE' && instrument.bse_scrip_code
                ? `BSE:${instrument.bse_scrip_code}`
                : `${instrument.exchange}:${instrument.symbol}`
            }
          />

          <div className="demo-head" style={{ fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--color-neutral-600)', margin: '20px 0 8px' }}>
            Recent price history
          </div>
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
            kicker="Volatility &amp; range"
            rows={[
              { label: 'ATR 14', value: fmtNum(ind?.atr_14) },
              { label: '20D vol. average', value: indianNum(ind?.volume_sma_20, 0) },
              { label: '52W high', value: fmtPrice(ind?.high_52w) },
              { label: '52W low', value: fmtPrice(ind?.low_52w) },
            ]}
          />
          {ind && <p className="text-muted" style={{ fontSize: 11, margin: 0 }}>Indicators as of {ind.trade_date}</p>}
        </div>
      </div>
    </div>
  )
}
