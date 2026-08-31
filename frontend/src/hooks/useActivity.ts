import { useCallback, useEffect, useState } from 'react'
import { getActivity } from '../services/api'
import type { ActivityResponse } from '../types/activity'

const POLL_INTERVAL_MS = 10_000

interface UseActivityResult {
  data: ActivityResponse | null
  loading: boolean
  error: string | null
  refresh: () => void
}

/** Loads `/api/activity` and re-polls it every `POLL_INTERVAL_MS` so the feed picks up
 * tool/LLM calls made from other tabs, routines, the scheduler, or another platform
 * (Discord, desktop, voice) without a manual refresh -- same shape as useLlmUsage.ts.
 */
export function useActivity(limit = 50): UseActivityResult {
  const [data, setData] = useState<ActivityResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false

    function load(isFirstLoad: boolean) {
      if (isFirstLoad) setLoading(true)
      getActivity(limit)
        .then((response) => {
          if (cancelled) return
          setData(response)
          setError(null)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setError(err instanceof Error ? err.message : 'Unknown error')
        })
        .finally(() => {
          if (!cancelled && isFirstLoad) setLoading(false)
        })
    }

    load(true)
    const interval = setInterval(() => load(false), POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [limit, version])

  return { data, loading, error, refresh }
}
