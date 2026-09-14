import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../lib/api'
import { IconSearch } from '../lib/icons'
import type { InstrumentOut, Page, ScreenOut } from '../lib/types'

export interface PaletteNavItem {
  to: string
  label: string
}

interface PaletteEntry {
  key: string
  title: string
  meta?: string
  go: () => void
}

// Debounced so every keystroke doesn't fire an ilike scan across ~7,500
// instruments -- 200ms is short enough to feel live, long enough to collapse
// a fast typist's keystrokes into one request.
const SYMBOL_DEBOUNCE_MS = 200

export function CommandPalette({
  open,
  onClose,
  pages,
}: {
  open: boolean
  onClose: () => void
  pages: PaletteNavItem[]
}) {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [symbols, setSymbols] = useState<InstrumentOut[]>([])
  const [screens, setScreens] = useState<ScreenOut[]>([])
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActiveIndex(0)
    inputRef.current?.focus()
    // Screens are few (a handful of saved screens per user) -- fetch once
    // per open and filter client-side rather than round-tripping per keystroke.
    apiFetch<Page<ScreenOut>>('/api/screens?limit=100')
      .then((p) => setScreens(p.items))
      .catch(() => setScreens([]))
  }, [open])

  useEffect(() => {
    if (!open || query.trim().length === 0) {
      setSymbols([])
      return
    }
    let cancelled = false
    const id = setTimeout(() => {
      apiFetch<Page<InstrumentOut>>(`/api/instruments?q=${encodeURIComponent(query.trim())}&limit=6`)
        .then((p) => !cancelled && setSymbols(p.items))
        .catch(() => !cancelled && setSymbols([]))
    }, SYMBOL_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [open, query])

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const pageEntries: PaletteEntry[] = pages
      .filter((p) => p.label.toLowerCase().includes(q))
      .map((p) => ({ key: `page:${p.to}`, title: p.label, go: () => navigate(p.to) }))

    const symbolEntries: PaletteEntry[] = symbols.map((s) => ({
      key: `symbol:${s.id}`,
      title: s.symbol,
      meta: s.company_name,
      go: () => navigate(`/stocks/${s.id}`),
    }))

    const screenEntries: PaletteEntry[] = screens
      .filter((s) => s.name.toLowerCase().includes(q))
      .map((s) => ({ key: `screen:${s.id}`, title: s.name, meta: 'Saved screen', go: () => navigate('/screener') }))

    return [
      { label: 'Pages', items: pageEntries },
      { label: 'Symbols', items: symbolEntries },
      { label: 'Screens', items: screenEntries },
    ].filter((g) => g.items.length > 0)
  }, [pages, symbols, screens, query, navigate])

  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups])

  // Reset on every actual input to the search (not flat.length, which can
  // briefly repeat a prior value while symbols are still in flight and would
  // leave a stale mid-list index highlighted instead of the top result).
  useEffect(() => {
    setActiveIndex(0)
  }, [query, symbols, screens])

  if (!open) return null

  function activate(entry: PaletteEntry) {
    entry.go()
    onClose()
  }

  return (
    <div
      role="presentation"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(6,6,12,0.62)', backdropFilter: 'blur(3px)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '96px 24px 24px', zIndex: 'var(--z-modal)' as unknown as number,
      }}
    >
      <div
        role="dialog"
        aria-label="Search"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 560, background: 'var(--color-surface)', border: '1px solid var(--color-divider)',
          borderRadius: 22, boxShadow: 'var(--shadow-lg)', overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '16px 18px', borderBottom: '1px solid var(--color-divider)' }}>
          <span style={{ color: 'var(--color-neutral-600)', display: 'flex' }}>
            <IconSearch size={16} />
          </span>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                onClose()
              } else if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActiveIndex((i) => Math.min(i + 1, flat.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActiveIndex((i) => Math.max(i - 1, 0))
              } else if (e.key === 'Enter' && flat[activeIndex]) {
                e.preventDefault()
                activate(flat[activeIndex])
              }
            }}
            placeholder="Search symbols, screens and pages"
            style={{ flex: 1, minWidth: 0, background: 'none', border: 'none', color: 'var(--color-text)', fontSize: 16, outline: 'none' }}
          />
          <button
            type="button"
            onClick={onClose}
            style={{ background: 'var(--color-surface-2)', color: 'var(--color-neutral-600)', border: 'none', borderRadius: 8, fontSize: 11.5, fontWeight: 600, padding: '5px 9px', cursor: 'pointer' }}
          >
            ESC
          </button>
        </div>

        <div style={{ maxHeight: 400, overflowY: 'auto', padding: 10 }}>
          {flat.length === 0 && (
            <div style={{ padding: '24px 10px', fontSize: 13.5, color: 'var(--color-neutral-600)', textAlign: 'center' }}>
              {query.trim() ? 'Nothing matched.' : 'Start typing to search.'}
            </div>
          )}
          {groups.map((group) => (
            <div key={group.label} style={{ marginBottom: 6 }}>
              <div style={{ fontSize: 11.5, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--color-neutral-600)', padding: '10px 10px 8px' }}>
                {group.label}
              </div>
              {group.items.map((entry) => {
                const index = flat.indexOf(entry)
                const isActive = index === activeIndex
                return (
                  <a
                    key={entry.key}
                    href="#"
                    onClick={(e) => {
                      e.preventDefault()
                      activate(entry)
                    }}
                    onMouseEnter={() => setActiveIndex(index)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 12, padding: '10px 10px', borderRadius: 13,
                      background: isActive ? 'var(--color-surface-2)' : 'transparent', color: 'var(--color-text)', textDecoration: 'none',
                    }}
                  >
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <span style={{ display: 'block', fontSize: 14.5, fontWeight: 600, color: 'var(--color-text)' }}>{entry.title}</span>
                      {entry.meta && (
                        <span style={{ display: 'block', fontSize: 12.5, color: 'var(--color-neutral-600)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {entry.meta}
                        </span>
                      )}
                    </span>
                  </a>
                )
              })}
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '11px 18px', borderTop: '1px solid var(--color-divider)', background: 'var(--color-surface-2)' }}>
          <span style={{ fontSize: 12, color: 'var(--color-neutral-600)' }}>
            <strong style={{ color: 'var(--color-neutral-800)' }}>↑↓</strong> move
          </span>
          <span style={{ fontSize: 12, color: 'var(--color-neutral-600)' }}>
            <strong style={{ color: 'var(--color-neutral-800)' }}>↵</strong> open
          </span>
          <span style={{ fontSize: 12, color: 'var(--color-neutral-600)' }}>
            <strong style={{ color: 'var(--color-neutral-800)' }}>⌘K</strong> toggle
          </span>
        </div>
      </div>
    </div>
  )
}
