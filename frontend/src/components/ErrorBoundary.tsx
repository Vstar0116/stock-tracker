import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Corners } from './Blueprint'

/** Without this, one render error blanks the whole app with no way back --
 *  React unmounts the tree and the user sees white. Scoped around the routed
 *  page only, so the shell (nav, log out) survives the crash. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('page crashed:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="card blueprint" style={{ maxWidth: 520, padding: 28 }} role="alert">
        <Corners />
        <div className="card-title">This page hit an error</div>
        <p className="card-body">
          Nothing was saved or changed. Try again, and if it keeps happening the details are in the browser console.
        </p>
        <p className="text-muted" style={{ fontSize: 12, margin: 0 }}>{error.message}</p>
        <button
          type="button"
          className="btn btn-primary blueprint"
          style={{ alignSelf: 'flex-start', marginTop: 4 }}
          onClick={() => this.setState({ error: null })}
        >
          <Corners />
          Try again
        </button>
      </div>
    )
  }
}
