import { useCallback, useEffect, useState } from 'react'
import { apiFetch, ApiError } from './api'

// Shared by every list/detail page below -- same fetch-on-mount,
// fetch-on-dep-change, loading/error/reload shape everywhere.
export function useFetch<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    if (path === null) {
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    apiFetch<T>(path)
      .then((res) => !cancelled && setData(res))
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof ApiError ? err.message : 'failed to load')
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps])

  return { data, loading, error, reload }
}
