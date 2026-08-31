import { useCallback, useEffect, useState } from 'react'
import { getProjectRoots, getProjects, setProjectRoots } from '../services/api'
import type { Project } from '../types/project'

interface UseProjectsResult {
  projects: Project[]
  roots: string[]
  loading: boolean
  error: string | null
  refresh: () => void
  saveRoots: (roots: string[]) => Promise<void>
}

/** Loads the discovered project list plus its configured scan-root folders
 * (`GET /api/projects`, `GET /api/projects/roots`) and re-fetches both after `saveRoots`
 * -- same load/refresh pattern as `useRoutines`/`useMemorySettings`. */
export function useProjects(): UseProjectsResult {
  const [projects, setProjects] = useState<Project[]>([])
  const [roots, setRoots] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([getProjects(), getProjectRoots()])
      .then(([projectData, rootsData]) => {
        if (cancelled) return
        setProjects(projectData)
        setRoots(rootsData.roots)
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

  const saveRoots = useCallback(
    async (newRoots: string[]) => {
      await setProjectRoots(newRoots)
      refresh()
    },
    [refresh],
  )

  return { projects, roots, loading, error, refresh, saveRoots }
}
