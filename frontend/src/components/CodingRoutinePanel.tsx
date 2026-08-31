import { useEffect, useMemo, useState } from 'react'
import { Play, Plus, Rocket, Trash2 } from 'lucide-react'
import { useProjects } from '../hooks/useProjects'
import { getApplicationMappings } from '../services/api'
import type { ApplicationMapping } from '../types/memory'
import type { Routine, RoutineRunResult, RoutineStep } from '../types/routine'
import { Button, Input, Panel, Select, useToast } from './ui'
import { cn } from './ui/utils'

/** The alias `open_application` resolves the editor step's `app_name` against
 * (`app/tools/applications.py`'s `DEFAULT_APPLICATION_ALIASES`). Fixed rather than
 * user-picked -- this template is specifically "open VS Code on today's project", not a
 * general first-step picker; the raw step editor below still covers editing that by hand.
 */
const EDITOR_APP_NAME = 'vscode'
const EDITOR_ALIAS_VARIANTS = new Set(['vscode', 'vs code', 'visual studio code'])
const CUSTOM_PROJECT_VALUE = '__custom__'

interface ExtraWindow {
  app_name: string
  target: string
}

/** Parses a persisted "coding" routine's steps back into the builder's shape. Steps
 * *are* the saved template -- there's no separate "last used project/windows" memory
 * entry to keep in sync -- so this is the one place that has to agree with
 * `buildCodingSteps` below on the shape: step 0 is the editor (`open_application` /
 * `vscode`, `target` = project path); every step after that is one "extra window".
 */
function parseCodingSteps(steps: RoutineStep[]): { projectPath: string; extraWindows: ExtraWindow[] } {
  const [first, ...rest] = steps
  const projectPath =
    first && first.tool_name === 'open_application' ? String(first.params.target ?? '') : ''
  const extraWindows = rest
    .filter((step) => step.tool_name === 'open_application')
    .map((step) => ({
      app_name: String(step.params.app_name ?? ''),
      target: String(step.params.target ?? ''),
    }))
  return { projectPath, extraWindows }
}

function buildCodingSteps(projectPath: string, extraWindows: ExtraWindow[]): RoutineStep[] {
  const editorParams: Record<string, unknown> = { app_name: EDITOR_APP_NAME }
  if (projectPath) editorParams.target = projectPath
  const steps: RoutineStep[] = [{ tool_name: 'open_application', params: editorParams }]
  for (const window of extraWindows) {
    if (!window.app_name) continue
    const params: Record<string, unknown> = { app_name: window.app_name }
    if (window.target) params.target = window.target
    steps.push({ tool_name: 'open_application', params })
  }
  return steps
}

interface CodingRoutinePanelProps {
  /** The persisted "coding" routine, once `useRoutines` has loaded it -- `undefined`
   * only while routines are still loading or (edge case) if it's been deleted. */
  routine: Routine | undefined
  loading: boolean
  /** Saves `steps` as the "coding" routine (creating it if it doesn't exist yet, since
   * a user could have deleted it) and runs it -- same create-then-run split
   * `RoutinesPage` already does for every other routine, just driven by this builder's
   * friendlier inputs instead of the raw step editor. */
  onSaveAndRun: (steps: RoutineStep[]) => Promise<RoutineRunResult>
}

/** A purpose-built "start my coding session" template on top of the generic routine
 * system: pick today's project from folders discovered under the configured scan roots
 * (Settings page), add whichever extra windows (browser, Spotify, ...) should open
 * alongside VS Code, and start it all in one click. Persists into the same "coding"
 * routine the generic list below (and voice/chat's "start coding") already run --
 * this is a friendlier front end for building its steps, not a separate routine
 * concept, so the raw step editor stays available for anything this builder doesn't
 * cover.
 */
export function CodingRoutinePanel({ routine, loading, onSaveAndRun }: CodingRoutinePanelProps) {
  const { projects, loading: projectsLoading, error: projectsError } = useProjects()
  const { show } = useToast()

  const [applications, setApplications] = useState<Record<string, ApplicationMapping>>({})

  useEffect(() => {
    let cancelled = false
    getApplicationMappings()
      .then((apps) => {
        if (!cancelled) setApplications(apps)
      })
      .catch(() => undefined) // best-effort -- the "add window" picker just stays empty
    return () => {
      cancelled = true
    }
  }, [])

  const parsed = useMemo(() => parseCodingSteps(routine?.steps ?? []), [routine])

  const [initialized, setInitialized] = useState(false)
  const [selectedProjectValue, setSelectedProjectValue] = useState<string>(CUSTOM_PROJECT_VALUE)
  const [customPath, setCustomPath] = useState('')
  const [extraWindows, setExtraWindows] = useState<ExtraWindow[]>([])
  const [addAppName, setAddAppName] = useState('')
  const [addTarget, setAddTarget] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RoutineRunResult | null>(null)

  // Seed the builder's fields from whatever's currently persisted, once both the
  // routine and the discovered project list have loaded -- after that, this is the
  // user's own in-progress edit, not something to keep overwriting on every refetch.
  useEffect(() => {
    if (initialized || loading || projectsLoading) return
    setExtraWindows(parsed.extraWindows)
    const match = projects.find((p) => p.path === parsed.projectPath)
    if (match) {
      setSelectedProjectValue(match.path)
    } else if (parsed.projectPath) {
      setSelectedProjectValue(CUSTOM_PROJECT_VALUE)
      setCustomPath(parsed.projectPath)
    } else if (projects[0]) {
      setSelectedProjectValue(projects[0].path)
    }
    setInitialized(true)
  }, [initialized, loading, projectsLoading, projects, parsed])

  const projectPath = selectedProjectValue === CUSTOM_PROJECT_VALUE ? customPath.trim() : selectedProjectValue

  const appOptions = useMemo(
    () => Object.keys(applications).filter((alias) => !EDITOR_ALIAS_VARIANTS.has(alias)).sort(),
    [applications],
  )
  const effectiveAddAppName = addAppName || appOptions[0] || ''

  function addWindow() {
    if (!effectiveAddAppName) return
    setExtraWindows((prev) => [...prev, { app_name: effectiveAddAppName, target: addTarget.trim() }])
    setAddTarget('')
  }

  function removeWindow(index: number) {
    setExtraWindows((prev) => prev.filter((_, i) => i !== index))
  }

  async function handleStart() {
    if (!projectPath) {
      show("Pick a project (or enter a custom path) first.", 'danger')
      return
    }
    setRunning(true)
    setResult(null)
    try {
      const steps = buildCodingSteps(projectPath, extraWindows)
      const runResult = await onSaveAndRun(steps)
      setResult(runResult)
      show(
        runResult.success ? 'Coding routine started.' : runResult.error ?? 'Failed to start coding routine.',
        runResult.success ? 'success' : 'danger',
      )
    } catch (err) {
      show(err instanceof Error ? err.message : 'Failed to start coding routine.', 'danger')
    } finally {
      setRunning(false)
    }
  }

  const stopped = routine !== undefined && !routine.enabled

  return (
    <Panel className="p-4">
      <div className="flex items-center gap-2">
        <Rocket className="h-4 w-4 text-primary" />
        <h2 className="font-display text-sm font-semibold text-text">Coding routine</h2>
      </div>
      <p className="mt-1 text-xs text-text-muted">
        Pick today's project and whatever else should open alongside VS Code, then start it in one
        click. This saves into the "coding" routine below -- editing its steps directly there
        works too.
      </p>

      <div className="mt-3">
        <label className="block text-xs font-medium text-text-muted">Today's project</label>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <div className="w-64">
            <Select
              value={selectedProjectValue}
              onChange={(e) => setSelectedProjectValue(e.target.value)}
              disabled={projectsLoading}
            >
              {projects.map((project) => (
                <option key={project.path} value={project.path}>
                  {project.name}
                </option>
              ))}
              <option value={CUSTOM_PROJECT_VALUE}>Custom path…</option>
            </Select>
          </div>
          {selectedProjectValue === CUSTOM_PROJECT_VALUE && (
            <Input
              className="w-64 font-mono text-xs"
              value={customPath}
              onChange={(e) => setCustomPath(e.target.value)}
              placeholder="C:\Coding\my-project"
            />
          )}
        </div>
        {projectsError && (
          <p className="mt-1 text-xs text-warning">
            Couldn't load discovered projects ({projectsError}) -- enter a path manually, or fix the
            scan folders in Settings.
          </p>
        )}
        {!projectsLoading && !projectsError && projects.length === 0 && (
          <p className="mt-1 text-xs text-text-muted">
            No project folders found under the configured scan folders (Settings) -- enter a custom
            path instead.
          </p>
        )}
      </div>

      <div className="mt-4">
        <label className="block text-xs font-medium text-text-muted">Windows to open alongside VS Code</label>
        <div className="mt-1 space-y-2">
          {extraWindows.length === 0 && <p className="text-xs text-text-muted">Just VS Code, for now.</p>}
          {extraWindows.map((window, index) => (
            <div
              key={index}
              className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-2/60 p-2"
            >
              <span className="text-xs text-text">{window.app_name}</span>
              {window.target && <span className="font-mono text-xs text-text-muted">{window.target}</span>}
              <Button type="button" variant="ghost" onClick={() => removeWindow(index)} aria-label="Remove window">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <div className="w-40">
            <Select value={effectiveAddAppName} onChange={(e) => setAddAppName(e.target.value)} disabled={appOptions.length === 0}>
              {appOptions.map((alias) => (
                <option key={alias} value={alias}>
                  {alias}
                </option>
              ))}
            </Select>
          </div>
          <Input
            className="w-56"
            value={addTarget}
            onChange={(e) => setAddTarget(e.target.value)}
            placeholder="optional URL (e.g. for a browser)"
          />
          <Button type="button" variant="ghost" onClick={addWindow} disabled={appOptions.length === 0}>
            <Plus className="h-3.5 w-3.5" />
            Add window
          </Button>
        </div>
        {appOptions.length === 0 && (
          <p className="mt-1 text-xs text-text-muted">
            No other application mappings registered yet -- add one (e.g. "chrome", "spotify") in
            Settings.
          </p>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button
          onClick={handleStart}
          disabled={running || stopped}
          loading={running}
          title={stopped ? 'The "coding" routine is stopped -- start it again below to run it.' : undefined}
        >
          <Play className="h-3.5 w-3.5" />
          Start coding
        </Button>
        {stopped && (
          <span className="text-xs text-warning">
            The "coding" routine is currently stopped -- start it again below first.
          </span>
        )}
      </div>

      {result && (
        <div
          className={cn(
            'mt-3 rounded-md border p-2 text-xs',
            result.success ? 'border-success/50 bg-success/10 text-success' : 'border-danger/50 bg-danger/10 text-danger',
          )}
        >
          {result.success ? result.message : result.error}
        </div>
      )}
    </Panel>
  )
}
