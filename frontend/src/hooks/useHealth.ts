import { useEffect, useState } from 'react'
import { getHealth } from '../services/api'
import type { HealthResponse } from '../types/health'

type HealthState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: HealthResponse }

export function useHealth(): HealthState {
  const [state, setState] = useState<HealthState>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data })
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            status: 'error',
            message: err instanceof Error ? err.message : 'Unknown error',
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
