import { useState } from 'react'
import type { FormEvent } from 'react'
import { ArrowDown, ArrowUp, Plus, Repeat, Trash2 } from 'lucide-react'
import { useRoutines } from '../hooks/useRoutines'
import type { Routine, RoutineRunResult, RoutineStep, ToolInfo } from '../types/routine'
import { Button, ConfirmDialog, Input, Panel, Select, Skeleton, StaggerItem, StaggerList, useToast } from '../components/ui'
import { cn } from '../components/ui/utils'

/** A step mid-edit: params are kept as raw JSON text so the textarea doesn't fight the
 * user while they're typing, and parsed/validated only when the routine is saved.
 */
interface EditableStep {
  tool_name: string
  paramsText: string
}

function toEditable(steps: RoutineStep[]): EditableStep[] {
  return steps.map((step) => ({ tool_name: step.tool_name, paramsText: JSON.stringify(step.params) }))
}

function parseSteps(steps: EditableStep[]): { steps: RoutineStep[] } | { error: string } {
  const parsed: RoutineStep[] = []
  for (const step of steps) {
    if (!step.tool_name) {
      return { error: 'Every step needs a tool.' }
    }
    let params: unknown
    try {
      params = step.paramsText.trim() ? JSON.parse(step.paramsText) : {}
    } catch {
      return { error: `Params for '${step.tool_name}' must be valid JSON.` }
    }
    if (typeof params !== 'object' || params === null || Array.isArray(params)) {
      return { error: `Params for '${step.tool_name}' must be a JSON object.` }
    }
    parsed.push({ tool_name: step.tool_name, params: params as Record<string, unknown> })
  }
  return { steps: parsed }
}

/** Tool names in `routine` that don't declare `"web"` in their `platforms` (§22, file
 * 12 prompt 2) -- e.g. `open_application`, `platforms=["desktop"]`. Purely advisory:
 * `ToolExecutor` (via `run_routine`'s inferred `RequesterContext.platform`, see
 * backend/app/api/routes/routines.py) is what actually enforces this, and correctly
 * still allows these when "Run now" is clicked from a same-machine (desktop) caller --
 * this just surfaces the same §22-style explanation *before* a remote/web caller
 * clicks the button, instead of only after the run comes back rejected.
 */
function desktopOnlyStepNames(routine: Routine, tools: ToolInfo[]): string[] {
  const platformsByTool = new Map(tools.map((tool) => [tool.name, tool.platforms]))
  const names = new Set<string>()
  for (const step of routine.steps) {
    const platforms = platformsByTool.get(step.tool_name)
    if (platforms && !platforms.includes('web')) names.add(step.tool_name)
  }
  return [...names]
}

function StepEditor({
  steps,
  tools,
  onChange,
}: {
  steps: EditableStep[]
  tools: ToolInfo[]
  onChange: (steps: EditableStep[]) => void
}) {
  function update(index: number, patch: Partial<EditableStep>) {
    onChange(steps.map((step, i) => (i === index ? { ...step, ...patch } : step)))
  }

  function remove(index: number) {
    onChange(steps.filter((_, i) => i !== index))
  }

  function move(index: number, delta: number) {
    const target = index + delta
    if (target < 0 || target >= steps.length) return
    const next = [...steps]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved)
    onChange(next)
  }

  function add() {
    onChange([...steps, { tool_name: tools[0]?.name ?? '', paramsText: '{}' }])
  }

  return (
    <div className="space-y-2">
      {steps.length === 0 && <p className="text-xs text-text-muted">No steps yet.</p>}
      {steps.map((step, index) => (
        <div
          key={index}
          className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-2/60 p-2"
        >
          <span className="text-xs text-text-muted">{index + 1}.</span>
          <div className="w-44">
            <Select value={step.tool_name} onChange={(e) => update(index, { tool_name: e.target.value })}>
              {step.tool_name && !tools.some((t) => t.name === step.tool_name) && (
                <option value={step.tool_name}>{step.tool_name} (unknown)</option>
              )}
              {tools.map((tool) => (
                <option key={tool.name} value={tool.name}>
                  {tool.name}
                </option>
              ))}
            </Select>
          </div>
          <Input
            className="min-w-[10rem] flex-1 font-mono text-xs"
            value={step.paramsText}
            onChange={(e) => update(index, { paramsText: e.target.value })}
            placeholder="{}"
          />
          <Button
            type="button"
            variant="ghost"
            onClick={() => move(index, -1)}
            disabled={index === 0}
            aria-label="Move step up"
          >
            <ArrowUp className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={() => move(index, 1)}
            disabled={index === steps.length - 1}
            aria-label="Move step down"
          >
            <ArrowDown className="h-3.5 w-3.5" />
          </Button>
          <Button type="button" variant="destructive" onClick={() => remove(index)}>
            <Trash2 className="h-3.5 w-3.5" />
            Remove
          </Button>
        </div>
      ))}
      <Button type="button" variant="ghost" onClick={add} disabled={tools.length === 0}>
        <Plus className="h-3.5 w-3.5" />
        Add step
      </Button>
    </div>
  )
}

export function RoutinesPage() {
  const { routines, tools, loading, error, create, updateSteps, remove: removeRoutine, run } = useRoutines()
  const { show } = useToast()

  const [newName, setNewName] = useState('')
  const [newSteps, setNewSteps] = useState<EditableStep[]>([])
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const [editingName, setEditingName] = useState<string | null>(null)
  const [editSteps, setEditSteps] = useState<EditableStep[]>([])
  const [editError, setEditError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [runResults, setRunResults] = useState<Record<string, RoutineRunResult>>({})
  const [runningName, setRunningName] = useState<string | null>(null)

  // Toast-based confirmation replacing `window.confirm()` (§5).
  const [pendingDelete, setPendingDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  async function handleCreate(event: FormEvent) {
    event.preventDefault()
    const name = newName.trim()
    if (!name) {
      setCreateError('A routine needs a name.')
      return
    }
    const parsed = parseSteps(newSteps)
    if ('error' in parsed) {
      setCreateError(parsed.error)
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      await create(name, parsed.steps)
      setNewName('')
      setNewSteps([])
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create routine.')
    } finally {
      setCreating(false)
    }
  }

  function startEdit(routine: Routine) {
    setEditingName(routine.name)
    setEditSteps(toEditable(routine.steps))
    setEditError(null)
  }

  async function handleSaveEdit() {
    if (!editingName) return
    const parsed = parseSteps(editSteps)
    if ('error' in parsed) {
      setEditError(parsed.error)
      return
    }
    setSaving(true)
    setEditError(null)
    try {
      await updateSteps(editingName, parsed.steps)
      setEditingName(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to update routine.')
    } finally {
      setSaving(false)
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    const name = pendingDelete
    setDeleting(true)
    try {
      await removeRoutine(name)
      if (editingName === name) setEditingName(null)
      show(`Routine '${name}' deleted.`, 'success')
    } finally {
      setDeleting(false)
      setPendingDelete(null)
    }
  }

  async function handleRun(name: string) {
    setRunningName(name)
    try {
      const result = await run(name)
      setRunResults((prev) => ({ ...prev, [name]: result }))
    } finally {
      setRunningName(null)
    }
  }

  return (
    <main className="px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <p className="text-sm text-text-muted">
          Named sequences of tool calls. Run one on demand, or edit its steps below.
        </p>

        {/* Skeleton loaders (§5) instead of a blank list while routines fetch --
            shaped like a routine card (name/meta line + button row). */}
        {loading && (
          <div className="mt-6 space-y-3">
            {[0, 1, 2].map((i) => (
              <Panel key={i} className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="space-y-2">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-24" />
                  </div>
                  <Skeleton className="h-8 w-20" />
                </div>
              </Panel>
            ))}
          </div>
        )}
        {error && <p className="mt-6 text-danger">Failed to load routines: {error}</p>}

        <div className="mt-6 space-y-3">
          {/* Empty-state illustration (§5), matching Tasks' treatment. */}
          {!loading && !error && routines.length === 0 && (
            <Panel className="flex flex-col items-center gap-2 border-dashed p-10 text-center">
              <Repeat className="h-8 w-8 text-text-muted" />
              <p className="text-sm text-text-muted">No routines yet -- create one below.</p>
            </Panel>
          )}

          {/* Staggered fade/slide-in on load (§5). */}
          <StaggerList className="space-y-3">
            {routines.map((routine) => {
            const result = runResults[routine.name]
            const desktopOnlySteps = desktopOnlyStepNames(routine, tools)
            return (
              <StaggerItem key={routine.name}>
              <Panel
                className="p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <span className="font-medium text-text">{routine.name}</span>
                    <span className="ml-2 text-xs text-text-muted">
                      {routine.steps.length} step{routine.steps.length === 1 ? '' : 's'} · {routine.trigger_type}
                    </span>
                    {desktopOnlySteps.length > 0 && (
                      <p className="mt-1 text-xs text-warning">
                        Includes desktop-only step{desktopOnlySteps.length === 1 ? '' : 's'} (
                        {desktopOnlySteps.join(', ')}) -- only runs when Jarvis is reached from your own
                        desktop, not over the web.
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button onClick={() => handleRun(routine.name)} disabled={runningName === routine.name} loading={runningName === routine.name}>
                      Run now
                    </Button>
                    {editingName === routine.name ? (
                      <Button variant="ghost" onClick={() => setEditingName(null)}>
                        Close editor
                      </Button>
                    ) : (
                      <Button variant="ghost" onClick={() => startEdit(routine)}>
                        Edit steps
                      </Button>
                    )}
                    <Button variant="destructive" onClick={() => setPendingDelete(routine.name)}>
                      Delete
                    </Button>
                  </div>
                </div>

                {editingName === routine.name ? (
                  <div className="mt-3 border-t border-border pt-3">
                    <StepEditor steps={editSteps} tools={tools} onChange={setEditSteps} />
                    {editError && <p className="mt-2 text-sm text-danger">{editError}</p>}
                    <div className="mt-3 flex gap-2">
                      <Button onClick={handleSaveEdit} disabled={saving} loading={saving}>
                        Save steps
                      </Button>
                    </div>
                  </div>
                ) : (
                  <ol className="mt-3 space-y-1 text-xs text-text-muted">
                    {routine.steps.map((step, index) => (
                      <li key={index}>
                        {index + 1}. <span className="text-text">{step.tool_name}</span>{' '}
                        <span className="font-mono">{JSON.stringify(step.params)}</span>
                      </li>
                    ))}
                  </ol>
                )}

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
              </StaggerItem>
            )
            })}
          </StaggerList>
        </div>

        <Panel className="mt-8 p-4">
          <form onSubmit={handleCreate}>
            <h2 className="font-display text-sm font-semibold text-text">New routine</h2>
            <Input
              className="mt-2 w-64"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="routine name"
            />
            <div className="mt-3">
              <StepEditor steps={newSteps} tools={tools} onChange={setNewSteps} />
            </div>
            {createError && <p className="mt-2 text-sm text-danger">{createError}</p>}
            <Button type="submit" disabled={creating} loading={creating} className="mt-3">
              Create routine
            </Button>
          </form>
        </Panel>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        message={`Delete routine '${pendingDelete}'? This cannot be undone.`}
        confirmLabel="Delete"
        confirmVariant="destructive"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </main>
  )
}
