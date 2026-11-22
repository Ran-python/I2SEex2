import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Calls `fetcher` immediately and then every `intervalMs` (paused while the tab is hidden).
 * Returns { data, error, loading, refresh }.
 */
export function usePolling(fetcher, intervalMs = 3000, { enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)

  useEffect(() => {
    fetcherRef.current = fetcher
  }, [fetcher])

  const refresh = useCallback(async () => {
    try {
      const result = await fetcherRef.current()
      setData(result)
      setError(null)
      return result
    } catch (err) {
      setError(err)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return undefined
    refresh()
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') refresh()
    }, intervalMs)
    const onVisible = () => document.visibilityState === 'visible' && refresh()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [enabled, intervalMs, refresh])

  return { data, error, loading, refresh }
}
