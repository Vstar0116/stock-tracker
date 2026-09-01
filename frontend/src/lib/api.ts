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

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  // FormData bodies (file uploads) must NOT get a manual Content-Type --
  // fetch sets multipart/form-data with the correct boundary itself, and
  // overriding it here would drop the boundary and break parsing server-side.
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })

  if (res.status === 401) {
    onUnauthorized?.()
    throw new ApiError(401, 'not authenticated')
  }
  if (res.status === 204) return undefined as T

  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const message = (body && typeof body.detail === 'string' ? body.detail : null) ?? res.statusText
    throw new ApiError(res.status, message)
  }
  return body as T
}

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`
}
