import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'

const ToastContext = createContext<((message: string) => void) | null>(null)

const VISIBLE_MS = 2200
const EXIT_MS = 180 // matches --dur; keep text mounted through the close transition

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState('')
  const [open, setOpen] = useState(false)
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const show = useCallback((msg: string) => {
    if (hideTimer.current) clearTimeout(hideTimer.current)
    if (clearTimer.current) clearTimeout(clearTimer.current)
    setMessage(msg)
    setOpen(true)
    hideTimer.current = setTimeout(() => setOpen(false), VISIBLE_MS)
  }, [])

  // Clear the text only once the close transition has had time to play, so
  // retriggering mid-exit doesn't reveal a blank bubble fading back in.
  useEffect(() => {
    if (open) return
    clearTimer.current = setTimeout(() => setMessage(''), EXIT_MS)
    return () => { if (clearTimer.current) clearTimeout(clearTimer.current) }
  }, [open])

  return (
    <ToastContext.Provider value={show}>
      {children}
      {/* Always mounted, even when empty: a live region has to exist in the DOM
          before the text lands in it, or the announcement is missed. */}
      <div role="status" aria-live="polite" className="toast-region">
        <div
          className="toast"
          data-open={open}
          style={{
            position: 'fixed', bottom: 22, right: 28, background: 'var(--toast-bg)', color: '#fff',
            padding: '10px 18px', fontSize: 13, boxShadow: 'var(--shadow-lg)', zIndex: 'var(--z-toast)',
          }}
        >
          {message}
        </div>
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): (message: string) => void {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
