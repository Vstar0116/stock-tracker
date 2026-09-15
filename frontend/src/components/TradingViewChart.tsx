import { useEffect, useRef } from 'react'

// TradingView's free "Advanced Chart" embed -- confirmed live via their
// widget builder (tradingview.com/widget/advanced-chart/) on 2026-08-20.
// The script reads its own textContent as the JSON config; there's no JS
// API to update it in place, so a symbol/study change just re-mounts the script.
const EMBED_SRC = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'

// TradingView's built-in "<Name>@tv-basicstudies" identifiers for the free
// widget's `studies` config array. The free tier caps a chart at 2 studies
// total (their own account-tier limit, not something this config can raise) --
// callers are expected to pass at most 2 ids.
export const TV_STUDY_OPTIONS: { id: string; label: string }[] = [
  { id: 'MASimple@tv-basicstudies', label: 'Moving Average (SMA)' },
  { id: 'MAExp@tv-basicstudies', label: 'Moving Average (EMA)' },
  { id: 'RSI@tv-basicstudies', label: 'RSI' },
  { id: 'MACD@tv-basicstudies', label: 'MACD' },
  { id: 'BB@tv-basicstudies', label: 'Bollinger Bands' },
  { id: 'StochasticRSI@tv-basicstudies', label: 'Stochastic RSI' },
  { id: 'ATR@tv-basicstudies', label: 'Average True Range' },
  { id: 'VWAP@tv-basicstudies', label: 'VWAP' },
]

export function TradingViewChart({ symbol, studies = [] }: { symbol: string; studies?: string[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const studiesKey = studies.join(',')

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // The rest of the app follows the OS theme; a light widget on a dark page
    // reads as broken, not as "the chart just doesn't do dark mode."
    const media = window.matchMedia('(prefers-color-scheme: dark)')

    function mount() {
      if (!container) return
      container.innerHTML = '<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>'
      const script = document.createElement('script')
      script.type = 'text/javascript'
      script.src = EMBED_SRC
      script.async = true
      script.text = JSON.stringify({
        autosize: true,
        symbol,
        interval: 'D',
        timezone: 'Etc/UTC',
        theme: media.matches ? 'dark' : 'light',
        style: '1',
        locale: 'en',
        allow_symbol_change: false,
        hide_side_toolbar: true,
        studies: studiesKey ? studiesKey.split(',') : [],
        support_host: 'https://www.tradingview.com',
      })
      container.appendChild(script)
    }

    mount()
    // No JS API to flip the embed's theme in place, so a system theme change
    // has to re-mount the script same as a symbol change would.
    media.addEventListener('change', mount)
    return () => media.removeEventListener('change', mount)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, studiesKey])

  return (
    <div style={{ height: 420, width: '100%', overflow: 'hidden' }}>
      <div ref={containerRef} className="tradingview-widget-container" style={{ height: '100%', width: '100%' }} />
    </div>
  )
}
