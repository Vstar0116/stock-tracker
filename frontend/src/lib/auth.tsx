import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { apiFetch, ApiError, setToken, setUnauthorizedHandler } from './api'
import type { LoginResponse, UserOut } from './types'

interface AuthContextValue {
  user: UserOut | null
  loading: boolean
  /** Set when the session ended on its own rather than by the user clicking
   *  Log out. Without it an expiry just dumps them on /login with no
   *  explanation, looking like the app forgot them at random. */
  expired: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null)
  const [loading, setLoading] = useState(false)
  const [expired, setExpired] = useState(false)
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const endSession = useCallback((wasExpiry: boolean) => {
    if (expiryTimer.current) clearTimeout(expiryTimer.current)
    setToken(null)
    setUser(null)
    setExpired(wasExpiry)
  }, [])

  const logout = useCallback(() => endSession(false), [endSession])

  useEffect(() => {
    // Any request that comes back 401 (token expired server-side, revoked
    // user, etc.) drops us back to a logged-out state -- no refresh token
    // to fall back to, so re-login is the only recovery.
    const handleExpiry = () => endSession(true)
    setUnauthorizedHandler(handleExpiry)
    return () => setUnauthorizedHandler(null)
  }, [endSession])

  const login = useCallback(
    async (email: string, password: string) => {
      setLoading(true)
      setExpired(false)
      try {
        const res = await apiFetch<LoginResponse>('/api/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password }),
        })
        setToken(res.access_token)
        const me = await apiFetch<UserOut>('/api/auth/me')
        setUser(me)

        // Proactively log out a few seconds before the JWT actually expires,
        // so a stale UI never sends a request we already know will 401.
        if (expiryTimer.current) clearTimeout(expiryTimer.current)
        expiryTimer.current = setTimeout(() => endSession(true), Math.max(res.expires_in - 5, 0) * 1000)
      } catch (err) {
        setToken(null)
        if (err instanceof ApiError) throw new Error(err.message)
        throw err
      } finally {
        setLoading(false)
      }
    },
    [endSession],
  )

  return <AuthContext.Provider value={{ user, loading, expired, login, logout }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const location = useLocation()
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />
  return <>{children}</>
}
