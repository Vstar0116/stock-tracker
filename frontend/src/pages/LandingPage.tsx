import { Link, Navigate } from 'react-router-dom'
import { IOSDeviceFrame } from '../components/IOSDeviceFrame'
import { useAuth } from '../lib/auth'
import { changeVisual, fmtPct, fmtPrice, indianNum } from '../lib/format'
import type { MarketMoverOut, MarketSnapshotOut } from '../lib/types'
import { useFetch } from '../lib/useFetch'

const STEPS = [
  { num: '20:30', title: 'Read the close', body: 'NSE and BSE bhavcopy for the trading day, Monday to Friday, skipped cleanly on a holiday.' },
  { num: '~20:33', title: 'Adjust the history', body: 'Splits, bonuses and dividends applied back through the price series.' },
  { num: '~20:34', title: 'Recompute indicators', body: 'Moving averages, RSI, MACD, ATR and 52-week range for every instrument.' },
  { num: '~20:38', title: 'Run your screens', body: 'Each active screen re-evaluated against the day it just ingested.' },
  { num: '~20:39', title: 'Raise what is new', body: 'Only matches that were not there yesterday become alerts.' },
]

function fmtDate(iso: string): string {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
}

function MoverRow({ m }: { m: MarketMoverOut }) {
  const v = changeVisual(m.change_pct)
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0' }}>
      <div style={{ width: 100, fontWeight: 600, fontSize: 14 }}>{m.symbol}</div>
      <div style={{ flex: 1, height: 8, borderRadius: 999, background: 'var(--color-neutral-800)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${Math.min(100, Math.abs(m.change_pct) * 12)}%`, background: v.color, borderRadius: 999 }} />
      </div>
      <div style={{ width: 66, textAlign: 'right', fontSize: 14, fontWeight: 600, color: v.color, fontVariantNumeric: 'tabular-nums' }}>{fmtPct(m.change_pct)}</div>
    </div>
  )
}

function TickerItem({ m }: { m: MarketMoverOut }) {
  const v = changeVisual(m.change_pct)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 8, padding: '0 26px', fontSize: 14.5, whiteSpace: 'nowrap' }}>
      <span style={{ fontWeight: 600 }}>{m.symbol}</span>
      <span style={{ color: 'var(--color-neutral-600)', fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(m.close)}</span>
      <span style={{ fontWeight: 600, color: v.color, fontVariantNumeric: 'tabular-nums' }}>{fmtPct(m.change_pct)}</span>
    </span>
  )
}

function LandingContent({ snap }: { snap: MarketSnapshotOut }) {
  const heroRows = snap.top_movers.slice(0, 4)
  const signalsToday = [
    { name: 'Golden Cross', detail: `${snap.golden_cross_count} instrument${snap.golden_cross_count === 1 ? '' : 's'} crossed above its 200-day average today` },
    { name: 'Volume Breakout ×3', detail: `${snap.volume_breakout_count} instrument${snap.volume_breakout_count === 1 ? '' : 's'} traded 3× its 20-day average volume` },
    { name: 'Alerts raised', detail: `${indianNum(snap.alerts_today, 0)} across every saved screen today` },
  ]
  const mobileHighlight = snap.golden_cross[0] ?? snap.volume_breakout[0]

  return (
    <div style={{ maxWidth: 1180, margin: '0 auto', padding: '0 24px' }}>
      <header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20, padding: '26px 0', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
          <div style={{ width: 34, height: 34, borderRadius: 11, background: 'var(--color-brand)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M3 17l5-6 4 3 4-7 5 5" /></svg>
          </div>
          <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: 20, letterSpacing: '-0.02em' }}>NSE Tracker</span>
        </div>
        <nav style={{ display: 'flex', alignItems: 'center', gap: 22, flexWrap: 'wrap' }}>
          <a href="#what" style={{ color: 'var(--color-neutral-700)', fontSize: 15, fontWeight: 500, whiteSpace: 'nowrap' }}>What it tracks</a>
          <a href="#evening" style={{ color: 'var(--color-neutral-700)', fontSize: 15, fontWeight: 500, whiteSpace: 'nowrap' }}>The evening run</a>
          <a href="#screens" style={{ color: 'var(--color-neutral-700)', fontSize: 15, fontWeight: 500, whiteSpace: 'nowrap' }}>Screens &amp; alerts</a>
          <Link to="/login" className="btn btn-primary" style={{ borderRadius: 999, padding: '11px 24px' }}>Sign in</Link>
        </nav>
      </header>

      <section style={{ padding: '68px 0 28px', display: 'grid', gridTemplateColumns: 'minmax(0, 1.05fr) minmax(0, 0.95fr)', gap: 56, alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: 'clamp(40px, 5.6vw, 64px)', lineHeight: 1, margin: '0 0 24px', textWrap: 'balance' }}>Your market evening, already sorted.</h1>
          <p style={{ fontSize: 19, lineHeight: 1.55, color: 'var(--color-neutral-700)', maxWidth: '30em', margin: '0 0 32px' }}>
            Every trading evening this reads the NSE and BSE close, adjusts for corporate actions, recomputes your indicators and runs your saved screens. By the time you sit down, the work is done.
          </p>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
            <Link to="/login" className="btn btn-primary" style={{ borderRadius: 999, padding: '17px 34px', fontSize: 17 }}>Sign in</Link>
            <a href="#what" className="btn btn-secondary" style={{ borderRadius: 999, padding: '16px 30px', fontSize: 17 }}>See what it tracks</a>
          </div>
          <p style={{ fontSize: 14, color: 'var(--color-neutral-600)', margin: '26px 0 0' }}>
            Accounts are created by an admin. There is no self-service sign-up, and no order placement anywhere in the product.
          </p>
        </div>
        <div className="card blueprint" style={{ padding: '30px 32px' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-neutral-600)', marginBottom: 20 }}>Close of {fmtDate(snap.as_of)}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4 }}>
            <span style={{ fontFamily: 'var(--font-heading)', fontSize: 50, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1 }}>{snap.golden_cross_count + snap.volume_breakout_count}</span>
            <span style={{ fontSize: 17, color: 'var(--color-neutral-700)', fontWeight: 500 }}>of {indianNum(snap.instrument_count, 0)} tracked instruments<br />crossed a line today</span>
          </div>
          <div style={{ height: 1, background: 'var(--color-divider)', margin: '26px 0 20px' }} />
          {heroRows.map((r) => {
            const v = changeVisual(r.change_pct)
            return (
              <div key={r.symbol + r.exchange} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '11px 0' }}>
                <div style={{ width: 40, height: 40, borderRadius: 13, background: 'var(--color-accent-100)', color: v.color, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: 15 }}>{r.symbol.slice(0, 2)}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 16 }}>{r.symbol}</div>
                  <div style={{ fontSize: 13, color: 'var(--color-neutral-600)' }}>{r.exchange}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontWeight: 600, fontSize: 16, fontVariantNumeric: 'tabular-nums' }}>{fmtPrice(r.close)}</div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: v.color, fontVariantNumeric: 'tabular-nums' }}>{fmtPct(r.change_pct)}</div>
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <div style={{ overflow: 'hidden', borderTop: '1px solid var(--color-divider)', borderBottom: '1px solid var(--color-divider)', padding: '18px 0', margin: '40px -24px 0' }}>
        <div className="ticker-rail" style={{ display: 'flex', width: 'max-content' }}>
          {snap.ticker.map((m, i) => <TickerItem key={m.symbol + i} m={m} />)}
          {snap.ticker.map((m, i) => <TickerItem key={m.symbol + 'b' + i} m={m} />)}
        </div>
      </div>

      <section id="what" style={{ padding: '104px 0 0' }}>
        <h2 style={{ fontSize: 'clamp(28px, 3.4vw, 42px)', lineHeight: 1.05, margin: '0 0 18px', maxWidth: '20em', textWrap: 'balance' }}>One screen answers the only question you have after close.</h2>
        <p style={{ fontSize: 18, color: 'var(--color-neutral-700)', maxWidth: '34em', margin: '0 0 44px', lineHeight: 1.55 }}>
          Real numbers from tonight's run: trend state, distance from the 50 and 200-day averages, and every screen that fired today.
        </p>

        <div style={{ borderRadius: 24, background: 'var(--color-neutral-900)', padding: '28px 30px', color: '#EDEBF8' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap', marginBottom: 26 }}>
            <div>
              <div style={{ fontSize: 13, color: '#9491AD', marginBottom: 6 }}>{fmtDate(snap.as_of)}</div>
              <div style={{ fontFamily: 'var(--font-heading)', fontSize: 28, fontWeight: 700 }}>Today's close</div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, background: 'rgba(75, 214, 155, 0.14)', color: '#4BD69B', borderRadius: 999, padding: '9px 16px', fontSize: 13, fontWeight: 600 }}>
              <span style={{ width: 7, height: 7, borderRadius: 999, background: '#4BD69B', display: 'block' }} />
              Real data, this trading day's pipeline run
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 26 }}>
            {[
              { label: 'Instruments tracked', value: indianNum(snap.instrument_count, 0) },
              { label: 'Moved 2% or more', value: indianNum(snap.moved_2pct_count, 0) },
              { label: 'Golden crosses', value: indianNum(snap.golden_cross_count, 0) },
              { label: 'Volume breakouts ×3', value: indianNum(snap.volume_breakout_count, 0) },
            ].map((s) => (
              <div key={s.label} style={{ background: '#211F30', borderRadius: 16, padding: '16px 18px' }}>
                <div style={{ fontSize: 12, color: '#9491AD', marginBottom: 8 }}>{s.label}</div>
                <div style={{ fontFamily: 'var(--font-heading)', fontSize: 26, fontWeight: 700 }}>{s.value}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr)', gap: 20 }}>
            <div>
              <div style={{ fontSize: 12, color: '#9491AD', marginBottom: 12 }}>Biggest movers across the whole market</div>
              {snap.top_movers.slice(0, 5).map((m) => <MoverRow key={m.symbol + m.exchange} m={m} />)}
            </div>
            <div>
              <div style={{ fontSize: 12, color: '#9491AD', marginBottom: 12 }}>Signals today</div>
              {signalsToday.map((s) => (
                <div key={s.name} style={{ background: '#211F30', borderRadius: 14, padding: '13px 15px', marginBottom: 9 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 3 }}>{s.name}</div>
                  <div style={{ fontSize: 12.5, color: '#9491AD' }}>{s.detail}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="screens" style={{ padding: '104px 0 0', display: 'grid', gridTemplateColumns: 'minmax(0, 1.25fr) minmax(0, 1fr)', gap: 24 }}>
        <div className="card blueprint" style={{ padding: 40 }}>
          <h3>Describe the screen. Keep the rule.</h3>
          <p style={{ fontSize: 17, color: 'var(--color-neutral-700)', lineHeight: 1.55, margin: '0 0 28px', maxWidth: '34em' }}>
            Type it in plain English and the rule appears in the builder for you to check. Nothing runs or saves until you say so, and every rule stays editable as AND/OR conditions.
          </p>
          <div style={{ background: 'var(--color-accent-100)', borderRadius: 16, padding: '18px 20px', fontSize: 16, color: 'var(--color-text)' }}>
            "pharma stocks below their 200 day average with RSI under 40"
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          <div style={{ background: 'var(--color-brand)', color: '#fff', borderRadius: 20, padding: 30 }}>
            <h3 style={{ color: '#fff', fontSize: 22 }}>Alerts you did not have to check for</h3>
            <p style={{ fontSize: 16, lineHeight: 1.55, margin: 0, color: 'rgba(255,255,255,0.85)' }}>
              {indianNum(snap.alerts_today, 0)} alerts raised across every saved screen at tonight's run. When a saved screen finds a symbol it did not match yesterday, it lands in Alerts with the numbers that triggered it.
            </p>
          </div>
          <div className="card blueprint" style={{ padding: 30 }}>
            <h3 style={{ fontSize: 22 }}>Whole-market scans</h3>
            <p style={{ fontSize: 16, color: 'var(--color-neutral-700)', lineHeight: 1.55, margin: 0 }}>
              Any fast/slow moving-average crossover, or the zone classifier, run across all {indianNum(snap.instrument_count, 0)} tracked instruments in a couple of seconds.
            </p>
          </div>
        </div>
      </section>

      <section id="evening" style={{ padding: '104px 0 0' }}>
        <h2 style={{ fontSize: 'clamp(28px, 3.4vw, 42px)', lineHeight: 1.05, margin: '0 0 14px', maxWidth: '20em', textWrap: 'balance' }}>What happens at 20:30 IST, every trading evening.</h2>
        <p style={{ fontSize: 18, color: 'var(--color-neutral-700)', maxWidth: '36em', margin: '0 0 48px', lineHeight: 1.55 }}>
          Both exchanges publish their bhavcopy well before this. If a run fails it retries twice more (5, then 15 minutes later) -- stale data is never left to be discovered days later.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 20 }}>
          {STEPS.map((st) => (
            <div key={st.num} className="card blueprint" style={{ padding: '26px 24px' }}>
              <div style={{ fontFamily: 'var(--font-heading)', fontSize: 15, fontWeight: 700, color: 'var(--color-brand)', marginBottom: 16 }}>{st.num}</div>
              <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 8 }}>{st.title}</div>
              <div style={{ fontSize: 14.5, color: 'var(--color-neutral-700)', lineHeight: 1.5 }}>{st.body}</div>
            </div>
          ))}
        </div>
      </section>

      <section style={{ padding: '104px 0 0', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 56, alignItems: 'center' }}>
        <div>
          <h2 style={{ fontSize: 'clamp(28px, 3.4vw, 42px)', lineHeight: 1.05, margin: '0 0 18px', textWrap: 'balance' }}>The same evening, in your pocket.</h2>
          <p style={{ fontSize: 18, color: 'var(--color-neutral-700)', margin: '0 0 24px', lineHeight: 1.55, maxWidth: '32em' }}>
            Open it on the train home and you get the short version: what moved, what fired, what needs a look. The full tables are there when you are back at a desk.
          </p>
        </div>
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <div style={{ transform: 'scale(0.68)', transformOrigin: 'top center' }}>
            <IOSDeviceFrame dark>
              <div style={{ padding: '58px 18px 46px', fontFamily: 'var(--font-body)', color: '#EDEBF8' }}>
                <div style={{ fontSize: 13, color: '#9491AD', marginBottom: 4 }}>{fmtDate(snap.as_of)} · close</div>
                <div style={{ fontFamily: 'var(--font-heading)', fontSize: 25, fontWeight: 700, lineHeight: 1.1, marginBottom: 18 }}>
                  {snap.moved_2pct_count} instruments moved 2% or more today.
                </div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
                  <div style={{ flex: 1, background: '#191927', borderRadius: 14, padding: '12px 13px' }}>
                    <div style={{ fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 700, color: '#4BD69B' }}>{snap.up_count}</div>
                    <div style={{ fontSize: 11.5, color: '#9491AD' }}>closed up</div>
                  </div>
                  <div style={{ flex: 1, background: '#191927', borderRadius: 14, padding: '12px 13px' }}>
                    <div style={{ fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 700, color: '#FF7A72' }}>{snap.down_count}</div>
                    <div style={{ fontSize: 11.5, color: '#9491AD' }}>closed down</div>
                  </div>
                  <div style={{ flex: 1, background: '#191927', borderRadius: 14, padding: '12px 13px' }}>
                    <div style={{ fontFamily: 'var(--font-heading)', fontSize: 20, fontWeight: 700, color: '#C7BBFF' }}>{indianNum(snap.alerts_today, 0)}</div>
                    <div style={{ fontSize: 11.5, color: '#9491AD' }}>alerts today</div>
                  </div>
                </div>
                <div style={{ background: '#191927', borderRadius: 18, padding: '16px 16px 8px', marginBottom: 14 }}>
                  <div style={{ fontSize: 12, color: '#9491AD', marginBottom: 10 }}>Biggest movers, whole market</div>
                  {snap.top_movers.slice(0, 4).map((m) => {
                    const v = changeVisual(m.change_pct)
                    return (
                      <div key={m.symbol + m.exchange} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, padding: '9px 0', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                        <span style={{ fontSize: 14, fontWeight: 600 }}>{m.symbol}</span>
                        <span style={{ display: 'flex', alignItems: 'baseline', gap: 10, fontVariantNumeric: 'tabular-nums' }}>
                          <span style={{ fontSize: 13.5, color: '#B6B2CE' }}>{fmtPrice(m.close)}</span>
                          <span style={{ fontSize: 13.5, fontWeight: 600, color: v.color, width: 58, textAlign: 'right' }}>{fmtPct(m.change_pct)}</span>
                        </span>
                      </div>
                    )
                  })}
                </div>
                {mobileHighlight && (
                  <div style={{ background: 'rgba(110,85,255,0.16)', border: '1px solid rgba(110,85,255,0.34)', borderRadius: 18, padding: '15px 16px' }}>
                    <div style={{ fontSize: 12, color: '#C7BBFF', fontWeight: 600, marginBottom: 5 }}>{snap.golden_cross[0] ? 'Golden Cross' : 'Volume Breakout ×3'}</div>
                    <div style={{ fontSize: 14, lineHeight: 1.45 }}>
                      {mobileHighlight.symbol} {snap.golden_cross[0] ? 'crossed its 200-day average today.' : 'traded 3× its 20-day average volume.'}
                    </div>
                  </div>
                )}
                <div style={{ background: '#6E55FF', color: '#fff', borderRadius: 999, textAlign: 'center', fontSize: 15, fontWeight: 600, padding: 15, marginTop: 16 }}>
                  Open all {indianNum(snap.instrument_count, 0)} instruments
                </div>
              </div>
            </IOSDeviceFrame>
          </div>
        </div>
      </section>

      <section style={{ padding: '104px 0 0' }}>
        <div style={{ background: 'var(--color-accent-100)', borderRadius: 28, padding: '56px 48px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 40 }}>
          {[
            { value: indianNum(snap.instrument_count, 0), label: 'Instruments tracked across NSE and BSE' },
            { value: indianNum(snap.golden_cross_count + snap.volume_breakout_count, 0), label: `Technical signals raised at the ${fmtDate(snap.as_of)} close` },
            { value: '3 retries', label: 'Before a failed evening run raises a webhook alert' },
            { value: '0', label: 'Orders placed -- this product only ever reads the market' },
          ].map((c) => (
            <div key={c.label}>
              <div style={{ fontFamily: 'var(--font-heading)', fontSize: 'clamp(30px, 3.4vw, 40px)', fontWeight: 700, lineHeight: 1, marginBottom: 10 }}>{c.value}</div>
              <div style={{ fontSize: 15, color: 'var(--color-neutral-700)', lineHeight: 1.45 }}>{c.label}</div>
            </div>
          ))}
        </div>
      </section>

      <section id="signin" style={{ padding: '104px 0 96px', textAlign: 'center' }}>
        <h2 style={{ fontSize: 'clamp(30px, 4vw, 48px)', lineHeight: 1.05, margin: '0 auto 20px', maxWidth: '24em', textWrap: 'balance' }}>Sit down at nine. Everything is already counted.</h2>
        <p style={{ fontSize: 18, color: 'var(--color-neutral-700)', maxWidth: '30em', margin: '0 auto 34px', lineHeight: 1.55 }}>Sign in with the account your admin created for you.</p>
        <Link to="/login" className="btn btn-primary" style={{ borderRadius: 999, padding: '19px 42px', fontSize: 18 }}>Sign in</Link>
      </section>

      <footer style={{ borderTop: '1px solid var(--color-divider)', padding: '34px 0 56px', display: 'flex', justifyContent: 'space-between', gap: 24, flexWrap: 'wrap' }}>
        <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: 16 }}>NSE Tracker</div>
        <p style={{ fontSize: 13.5, color: 'var(--color-neutral-600)', margin: 0, maxWidth: '44em', lineHeight: 1.55 }}>
          Internal tracking tool -- for informational purposes only. Not investment advice. No trading or order placement. Prices are end-of-day, adjusted for corporate actions.
        </p>
      </footer>
    </div>
  )
}

export function LandingPage() {
  const { user } = useAuth()
  const { data: snap, loading, error } = useFetch<MarketSnapshotOut>(user ? null : '/api/public/market-snapshot')

  if (user) return <Navigate to="/dashboard" replace />

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-bg)' }}>
      <style>{`
        @keyframes ticker-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
        .ticker-rail { animation: ticker-scroll 46s linear infinite; }
        @media (prefers-reduced-motion: reduce) { .ticker-rail { animation: none; } }
      `}</style>
      {loading && (
        <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', color: 'var(--color-neutral-600)' }}>Loading tonight's numbers…</div>
      )}
      {error && !loading && (
        <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
          <p className="text-muted">Couldn't load today's market numbers.</p>
          <Link to="/login" className="btn btn-primary" style={{ borderRadius: 999 }}>Sign in</Link>
        </div>
      )}
      {snap && !loading && !error && <LandingContent snap={snap} />}
    </div>
  )
}
