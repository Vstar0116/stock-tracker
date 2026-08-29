import { Component, type ErrorInfo, type ReactNode } from 'react'
import { getLastRequestId } from '../lib/api'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/** Class component is required here -- React has no hook equivalent for
 * getDerivedStateFromError/componentDidCatch. Before this, a throw in ANY
 * page component (or AppShell, which every protected route renders
 * through) blanked the entire app to a blank white screen with nothing but
 * a console error -- there was no boundary anywhere in the tree. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('Unhandled render error:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children

    const requestId = getLastRequestId()
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', padding: 24 }}>
        <div className="card blueprint" style={{ maxWidth: 440, padding: 28, textAlign: 'center' }}>
          <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
          <div className="card-kicker">Something went wrong</div>
          <div className="card-title" style={{ marginBottom: 4 }}>This page hit an unexpected error</div>
          <p className="card-body" style={{ marginBottom: 16 }}>
            Reloading usually clears it. If it keeps happening, mention this to whoever manages the app.
          </p>
          {requestId && (
            <p className="text-muted" style={{ fontSize: 11, marginBottom: 16, fontFamily: 'monospace' }}>
              reference: {requestId}
            </p>
          )}
          <button type="button" className="btn btn-primary blueprint" onClick={() => window.location.reload()}>
            <i className="corner tl" /><i className="corner tr" /><i className="corner bl" /><i className="corner br" />
            Reload
          </button>
        </div>
      </div>
    )
  }
}
