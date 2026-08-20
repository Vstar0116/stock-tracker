import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth'

export function LoginPage() {
  const { user, login, loading } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  if (user) {
    const from = (location.state as { from?: Location })?.from?.pathname ?? '/watchlists'
    return <Navigate to={from} replace />
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await login(email, password)
      navigate('/watchlists', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'login failed')
    }
  }

  return (
    <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center', justifyContent: 'center', background: 'var(--color-bg)' }}>
      <form onSubmit={handleSubmit} className="card blueprint" style={{ width: '100%', maxWidth: 360, padding: 32 }}>
        <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
        <h4 style={{ margin: '0 0 22px' }}>NSE TRACKER</h4>

        <div className="field" style={{ marginBottom: 14 }}>
          <label htmlFor="email">Email</label>
          <input id="email" type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} className="input" />
        </div>

        <div className="field" style={{ marginBottom: 14 }}>
          <label htmlFor="password">Password</label>
          <input id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} className="input" />
        </div>

        {error && <p style={{ marginBottom: 14, fontSize: 13, color: 'var(--color-neg-text)' }}>{error}</p>}

        <button type="submit" disabled={loading} className="btn btn-primary blueprint btn-block">
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          {loading ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="text-muted" style={{ marginTop: 16, fontSize: 11.5 }}>
          No self-service sign-up — ask an admin to create your account with the CLI.
        </p>
      </form>
    </div>
  )
}
