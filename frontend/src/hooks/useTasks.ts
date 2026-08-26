import { useCallback, useEffect, useState } from 'react'
import { completeTask, createTask, deleteTask, getTasks, updateTask } from '../services/api'
import type { Task, TaskCreateInput, TaskFilters, TaskUpdateInput } from '../types/task'

interface UseTasksResult {
  tasks: Task[]
  loading: boolean
  error: string | null
  filters: TaskFilters
  setFilters: (filters: TaskFilters) => void
  refresh: () => void
  create: (input: TaskCreateInput) => Promise<void>
  edit: (id: number, input: TaskUpdateInput) => Promise<void>
  complete: (id: number) => Promise<void>
  remove: (id: number) => Promise<void>
}

/** Loads tasks for the current filters and re-fetches after any mutation, so the list
 * shown always reflects the backend rather than an optimistic local guess.
 */
export function useTasks(initialFilters: TaskFilters = {}): UseTasksResult {
  const [filters, setFilters] = useState<TaskFilters>(initialFilters)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getTasks(filters)
      .then((data) => {
        if (cancelled) return
        setTasks(data)
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
  }, [filters, version])

  const create = useCallback(
    async (input: TaskCreateInput) => {
      await createTask(input)
      refresh()
    },
    [refresh],
  )

  const edit = useCallback(
    async (id: number, input: TaskUpdateInput) => {
      await updateTask(id, input)
      refresh()
    },
    [refresh],
  )

  const complete = useCallback(
    async (id: number) => {
      await completeTask(id)
      refresh()
    },
    [refresh],
  )

  const remove = useCallback(
    async (id: number) => {
      await deleteTask(id)
      refresh()
    },
    [refresh],
  )

  return { tasks, loading, error, filters, setFilters, refresh, create, edit, complete, remove }
}
