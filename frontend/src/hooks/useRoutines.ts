import { useCallback, useEffect, useState } from 'react'
import {
  createRoutine,
  deleteRoutine,
  getRoutines,
  getTools,
  runRoutine,
  setRoutineEnabled,
  updateRoutineSteps,
} from '../services/api'
import type { Routine, RoutineRunResult, RoutineStep, ToolInfo } from '../types/routine'

interface UseRoutinesResult {
  routines: Routine[]
  tools: ToolInfo[]
  loading: boolean
  error: string | null
  refresh: () => void
  create: (name: string, steps: RoutineStep[]) => Promise<void>
  updateSteps: (name: string, steps: RoutineStep[]) => Promise<void>
  remove: (name: string) => Promise<void>
  run: (name: string) => Promise<RoutineRunResult>
  setEnabled: (name: string, enabled: boolean) => Promise<void>
}

/** Loads routines plus the available tool catalog (for the step editor's tool picker),
 * and re-fetches both after any mutation.
 */
export function useRoutines(): UseRoutinesResult {
  const [routines, setRoutines] = useState<Routine[]>([])
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([getRoutines(), getTools()])
      .then(([routineData, toolData]) => {
        if (cancelled) return
        setRoutines(routineData)
        setTools(toolData)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Unknown error')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [version])

  const create = useCallback(
    async (name: string, steps: RoutineStep[]) => {
      await createRoutine(name, steps)
      refresh()
    },
    [refresh],
  )

  const updateSteps = useCallback(
    async (name: string, steps: RoutineStep[]) => {
      await updateRoutineSteps(name, steps)
      refresh()
    },
    [refresh],
  )

  const remove = useCallback(
    async (name: string) => {
      await deleteRoutine(name)
      refresh()
    },
    [refresh],
  )

  const run = useCallback(
    async (name: string) => {
      const result = await runRoutine(name)
      refresh()
      return result
    },
    [refresh],
  )

  const setEnabled = useCallback(
    async (name: string, enabled: boolean) => {
      await setRoutineEnabled(name, enabled)
      refresh()
    },
    [refresh],
  )

  return { routines, tools, loading, error, refresh, create, updateSteps, remove, run, setEnabled }
}
