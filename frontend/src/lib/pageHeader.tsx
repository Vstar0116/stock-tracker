import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

interface HeaderState {
  title: string
  subtitle?: string | null
}

const HeaderContext = createContext<{ header: HeaderState; setHeader: (h: HeaderState) => void } | null>(null)

export function HeaderProvider({ children }: { children: ReactNode }) {
  const [header, setHeader] = useState<HeaderState>({ title: '' })
  return <HeaderContext.Provider value={{ header, setHeader }}>{children}</HeaderContext.Provider>
}

/** Pages call this to publish their header title/subtitle up to AppShell --
 * lets the "Watchlists" tab title follow the active tab, the stock page
 * show the live symbol, etc., without AppShell needing route-specific logic. */
export function usePageHeader(title: string, subtitle?: string | null) {
  const ctx = useContext(HeaderContext)
  if (!ctx) throw new Error('usePageHeader must be used within HeaderProvider')
  const { setHeader } = ctx
  useEffect(() => {
    setHeader({ title, subtitle })
  }, [title, subtitle, setHeader])
}

export function useHeader(): HeaderState {
  const ctx = useContext(HeaderContext)
  if (!ctx) throw new Error('useHeader must be used within HeaderProvider')
  return ctx.header
}
