import { useCallback, useEffect, useState } from 'react'
import { getDiagnosticChecks, runDiagnostics } from '../services/api'
import type { DiagnosticCheck, DiagnosticsRunResult } from '../types/diagnostics'

interface UseDiagnosticsResult {
  checks: DiagnosticCheck[]
  loadingChecks: boolean
  checksError: string | null
  running: boolean
  runError: string | null
  result: DiagnosticsRunResult | null
  /** Runs every component when `selected` is omitted/empty, otherwise only the named
   * ones -- the "customize which component gets tested" case. */
  run: (selected?: string[]) => Promise<void>
}

/** Loads the component catalog once (for the checkbox list) and exposes `run()` to
 * fire the actual self-test battery on demand -- no polling, this only happens when the
 * user clicks the button.
 */
export function useDiagnostics(): UseDiagnosticsResult {
  const [checks, setChecks] = useState<DiagnosticCheck[]>([])
  const [loadingChecks, setLoadingChecks] = useState(true)
  const [checksError, setChecksError] = useState<string | null>(null)

  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [result, setResult] = useState<DiagnosticsRunResult | null>(null)

  useEffect(() => {
    let cancelled = false
    getDiagnosticChecks()
      .then((data) => {
        if (!cancelled) setChecks(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setChecksError(err instanceof Error ? err.message : 'Unknown error')
      })
      .finally(() => {
        if (!cancelled) setLoadingChecks(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const run = useCallback(async (selected?: string[]) => {
    setRunning(true)
    setRunError(null)
    try {
      const data = await runDiagnostics(selected && selected.length > 0 ? selected : undefined)
      setResult(data)
    } catch (err) {
      setRunError(err instanceof Error ? err.message : 'Failed to run diagnostics.')
    } finally {
      setRunning(false)
    }
  }, [])

  return { checks, loadingChecks, checksError, running, runError, result, run }
}
