import { useState } from 'react'
import type { FormEvent } from 'react'
import { useRoutines } from '../hooks/useRoutines'
import type { Routine, RoutineRunResult, RoutineStep, ToolInfo } from '../types/routine'

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
      {steps.length === 0 && <p className="text-xs text-slate-500">No steps yet.</p>}
      {steps.map((step, index) => (
        <div
          key={index}
          className="flex flex-wrap items-center gap-2 rounded border border-slate-700 bg-slate-800/60 p-2"
        >
          <span className="text-xs text-slate-500">{index + 1}.</span>
          <select
            className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-sm text-slate-100"
            value={step.tool_name}
            onChange={(e) => update(index, { tool_name: e.target.value })}
          >
            {step.tool_name && !tools.some((t) => t.name === step.tool_name) && (
              <option value={step.tool_name}>{step.tool_name} (unknown)</option>
            )}
            {tools.map((tool) => (
              <option key={tool.name} value={tool.name}>
                {tool.name}
              </option>
            ))}
          </select>
          <input
            className="min-w-[10rem] flex-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 font-mono text-xs text-slate-100"
            value={step.paramsText}
            onChange={(e) => update(index, { paramsText: e.target.value })}
            placeholder="{}"
          />
          <button
            type="button"
            onClick={() => move(index, -1)}
            disabled={index === 0}
            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-30"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => move(index, 1)}
            disabled={index === steps.length - 1}
            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-30"
          >
            ↓
          </button>
          <button
            type="button"
            onClick={() => remove(index)}
            className="rounded border border-red-800 px-2 py-1 text-xs text-red-400 hover:bg-red-900/40"
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        disabled={tools.length === 0}
        className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
      >
        + Add step
      </button>
    </div>
  )
}

export function RoutinesPage() {
  const { routines, tools, loading, error, create, updateSteps, remove: removeRoutine, run } = useRoutines()

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

  async function handleDelete(name: string) {
    if (!window.confirm(`Delete routine '${name}'? This cannot be undone.`)) return
    await removeRoutine(name)
    if (editingName === name) setEditingName(null)
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
    <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-4xl">
        <h1 className="text-2xl font-semibold">Routines</h1>
        <p className="mt-1 text-sm text-slate-400">
          Named sequences of tool calls. Run one on demand, or edit its steps below.
        </p>

        {loading && <p className="mt-6 text-slate-400">Loading routines…</p>}
        {error && <p className="mt-6 text-red-400">Failed to load routines: {error}</p>}

        <div className="mt-6 space-y-3">
          {!loading && !error && routines.length === 0 && (
            <p className="text-slate-400">No routines yet -- create one below.</p>
          )}

          {routines.map((routine) => {
            const result = runResults[routine.name]
            const desktopOnlySteps = desktopOnlyStepNames(routine, tools)
            return (
              <div key={routine.name} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <span className="font-medium">{routine.name}</span>
                    <span className="ml-2 text-xs text-slate-500">
                      {routine.steps.length} step{routine.steps.length === 1 ? '' : 's'} · {routine.trigger_type}
                    </span>
                    {desktopOnlySteps.length > 0 && (
                      <p className="mt-1 text-xs text-amber-400">
                        Includes desktop-only step{desktopOnlySteps.length === 1 ? '' : 's'} (
                        {desktopOnlySteps.join(', ')}) -- only runs when Jarvis is reached from your own
                        desktop, not over the web.
                      </p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleRun(routine.name)}
                      disabled={runningName === routine.name}
                      className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                    >
                      {runningName === routine.name ? 'Running…' : 'Run now'}
                    </button>
                    {editingName === routine.name ? (
                      <button
                        onClick={() => setEditingName(null)}
                        className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                      >
                        Close editor
                      </button>
                    ) : (
                      <button
                        onClick={() => startEdit(routine)}
                        className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                      >
                        Edit steps
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(routine.name)}
                      className="rounded border border-red-800 px-3 py-1 text-xs text-red-400 hover:bg-red-900/40"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {editingName === routine.name ? (
                  <div className="mt-3 border-t border-slate-800 pt-3">
                    <StepEditor steps={editSteps} tools={tools} onChange={setEditSteps} />
                    {editError && <p className="mt-2 text-sm text-red-400">{editError}</p>}
                    <div className="mt-3 flex gap-2">
                      <button
                        onClick={handleSaveEdit}
                        disabled={saving}
                        className="rounded bg-emerald-600 px-3 py-1 text-sm text-white hover:bg-emerald-500 disabled:opacity-50"
                      >
                        {saving ? 'Saving…' : 'Save steps'}
                      </button>
                    </div>
                  </div>
                ) : (
                  <ol className="mt-3 space-y-1 text-xs text-slate-400">
                    {routine.steps.map((step, index) => (
                      <li key={index}>
                        {index + 1}. <span className="text-slate-200">{step.tool_name}</span>{' '}
                        <span className="font-mono">{JSON.stringify(step.params)}</span>
                      </li>
                    ))}
                  </ol>
                )}

                {result && (
                  <div
                    className={`mt-3 rounded border p-2 text-xs ${
                      result.success
                        ? 'border-emerald-800 bg-emerald-950/40 text-emerald-300'
                        : 'border-red-800 bg-red-950/40 text-red-300'
                    }`}
                  >
                    {result.success ? result.message : result.error}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        <form onSubmit={handleCreate} className="mt-8 rounded-lg border border-slate-800 bg-slate-900 p-4">
          <h2 className="text-sm font-semibold text-slate-200">New routine</h2>
          <input
            className="mt-2 w-64 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-sm text-slate-100"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="routine name"
          />
          <div className="mt-3">
            <StepEditor steps={newSteps} tools={tools} onChange={setNewSteps} />
          </div>
          {createError && <p className="mt-2 text-sm text-red-400">{createError}</p>}
          <button
            type="submit"
            disabled={creating}
            className="mt-3 rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {creating ? 'Creating…' : 'Create routine'}
          </button>
        </form>
      </div>
    </main>
  )
}
