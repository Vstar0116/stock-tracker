import { useEffect, useRef } from 'react'

// TradingView's free "Advanced Chart" embed -- confirmed live via their
// widget builder (tradingview.com/widget/advanced-chart/) on 2026-08-20.
// The script reads its own textContent as the JSON config; there's no JS
// API to update it in place, so a symbol change just re-mounts the script.
const EMBED_SRC = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'

export function TradingViewChart({ symbol }: { symbol: string }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
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
      theme: 'light',
      style: '1',
      locale: 'en',
      allow_symbol_change: false,
      hide_side_toolbar: true,
      support_host: 'https://www.tradingview.com',
    })
    container.appendChild(script)
  }, [symbol])

  return (
    <div style={{ height: 420, width: '100%', overflow: 'hidden' }}>
      <div ref={containerRef} className="tradingview-widget-container" style={{ height: '100%', width: '100%' }} />
    </div>
  )
}
