import { useCallback, useEffect, useState } from 'react'
import { getLlmUsage } from '../services/api'
import type { LLMUsageResponse } from '../types/llmUsage'

const POLL_INTERVAL_MS = 15_000

interface UseLlmUsageResult {
  data: LLMUsageResponse | null
  loading: boolean
  error: string | null
  refresh: () => void
}

/** Loads `/api/llm/usage` and re-polls it every `POLL_INTERVAL_MS` so the status panel
 * reflects quota/health changes without a manual refresh -- both halves it reports
 * (today's usage totals, live QuotaManager/HealthManager status) can change from
 * requests this tab never made itself.
 */
export function useLlmUsage(): UseLlmUsageResult {
  const [data, setData] = useState<LLMUsageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false

    function load(isFirstLoad: boolean) {
      if (isFirstLoad) setLoading(true)
      getLlmUsage()
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
  }, [version])

  return { data, loading, error, refresh }
}
