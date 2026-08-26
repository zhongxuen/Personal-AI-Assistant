import { useCallback, useEffect, useState } from 'react'
import {
  deleteApplicationMapping,
  getApplicationMappings,
  getDefaultProject,
  setApplicationMapping,
  setDefaultProject,
} from '../services/api'
import type { ApplicationMapping } from '../types/memory'

interface UseMemorySettingsResult {
  applications: Record<string, ApplicationMapping>
  defaultProject: string
  loading: boolean
  error: string | null
  refresh: () => void
  saveApplication: (alias: string, mapping: ApplicationMapping) => Promise<void>
  removeApplication: (alias: string) => Promise<void>
  saveDefaultProject: (value: string) => Promise<void>
}

/** Loads the two memory-backed settings surfaces file 09 prompt 3 asks for --
 * application mappings and the "coding" routine's default project -- and re-fetches
 * both after any mutation, same load/refresh pattern as `useRoutines`.
 */
export function useMemorySettings(): UseMemorySettingsResult {
  const [applications, setApplications] = useState<Record<string, ApplicationMapping>>({})
  const [defaultProject, setDefaultProjectState] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([getApplicationMappings(), getDefaultProject()])
      .then(([apps, project]) => {
        if (cancelled) return
        setApplications(apps)
        setDefaultProjectState(project.default_project)
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

  const saveApplication = useCallback(
    async (alias: string, mapping: ApplicationMapping) => {
      await setApplicationMapping(alias, mapping)
      refresh()
    },
    [refresh],
  )

  const removeApplication = useCallback(
    async (alias: string) => {
      await deleteApplicationMapping(alias)
      refresh()
    },
    [refresh],
  )

  const saveDefaultProject = useCallback(
    async (value: string) => {
      await setDefaultProject(value)
      refresh()
    },
    [refresh],
  )

  return {
    applications,
    defaultProject,
    loading,
    error,
    refresh,
    saveApplication,
    removeApplication,
    saveDefaultProject,
  }
}
