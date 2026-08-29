// Thin fetch wrapper. Token lives only in this module's memory (never
// localStorage/sessionStorage) -- a page refresh always requires a fresh
// login, which is the point: nothing durable for an XSS payload to steal.

// 127.0.0.1, not "localhost" -- on Windows "localhost" often resolves to the
// IPv6 loopback first, and uvicorn's default bind (127.0.0.1 only) doesn't
// answer there, so fetches silently fail with ERR_EMPTY_RESPONSE.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

let token: string | null = null
let onUnauthorized: (() => void) | null = null

export function setToken(next: string | null) {
  token = next
}

// auth.tsx registers a callback (clear user, redirect to /login) that fires
// whenever any request comes back 401 -- covers both "token expired
// mid-session" and "token was never valid" in one place.
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

// The backend echoes a per-request correlation id (app/request_id.py) on
// every response, success or failure. Tracked here so ErrorBoundary can
// show a "reference: <id>" hint on an unrelated render crash -- best
// effort, not a guarantee the crash and this id are the same request, but
// usually they are (the crash typically follows right after the fetch that
// exposed the bad data).
let lastRequestId: string | null = null

export function getLastRequestId(): string | null {
  return lastRequestId
}

export class ApiError extends Error {
  status: number
  requestId: string | null
  constructor(status: number, message: string, requestId: string | null = null) {
    super(message)
    this.status = status
    this.requestId = requestId
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  // A FormData body (multipart upload) must NOT get this default -- fetch
  // sets its own Content-Type with the multipart boundary from the body
  // itself, and a hardcoded 'application/json' here would overwrite that
  // and break the upload server-side.
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })
  const requestId = res.headers.get('X-Request-ID')
  if (requestId) lastRequestId = requestId

  if (res.status === 401) {
    onUnauthorized?.()
    throw new ApiError(401, 'not authenticated', requestId)
  }
  if (res.status === 204) return undefined as T

  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const message = (body && typeof body.detail === 'string' ? body.detail : null) ?? res.statusText
    throw new ApiError(res.status, message, requestId)
  }
  return body as T
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}
