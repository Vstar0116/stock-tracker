import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'

const ToastContext = createContext<((message: string) => void) | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const show = useCallback((msg: string) => {
    setMessage(msg)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setMessage(null), 2200)
  }, [])

  return (
    <ToastContext.Provider value={show}>
      {children}
      {/* Always mounted, even when empty: a live region has to exist in the DOM
          before the text lands in it, or the announcement is missed. */}
      <div role="status" aria-live="polite" className="toast-region">
        {message && (
          <div
            className="toast"
            style={{
              position: 'fixed', bottom: 22, right: 28, background: 'var(--toast-bg)', color: '#fff',
              padding: '10px 18px', fontSize: 13, boxShadow: 'var(--shadow-lg)', zIndex: 'var(--z-toast)',
            }}
          >
            {message}
          </div>
        )}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): (message: string) => void {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
