import { useState } from 'react'
import type { FormEvent } from 'react'
import { motion } from 'framer-motion'
import { Check, ListChecks } from 'lucide-react'
import { useTasks } from '../hooks/useTasks'
import type { Task, TaskFilters } from '../types/task'
import { Badge, Button, ConfirmDialog, Input, Panel, Select, Skeleton, StaggerItem, StaggerList, useToast } from '../components/ui'
import { cn } from '../components/ui/utils'

const STATUS_OPTIONS = ['pending', 'completed']
const PRIORITY_OPTIONS = ['low', 'medium', 'high']

interface TaskFormValues {
  title: string
  due: string
  category: string
  priority: string
}

const EMPTY_FORM: TaskFormValues = { title: '', due: '', category: '', priority: 'medium' }

function formatDue(due: string | null): string {
  if (!due) return '—'
  const date = new Date(due)
  if (Number.isNaN(date.getTime())) return due
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/** Circular checkbox affix replacing the old plain "Complete" text button (§5:
 * "animated checkbox/complete state"). Same `onComplete` handler/behavior as before --
 * only the affordance is new. There's no "uncomplete" action in the data layer, so the
 * checked state renders as a static badge with a draw-in checkmark rather than a
 * clickable toggle.
 */
function CompleteCheckbox({
  completed,
  busy,
  onComplete,
}: {
  completed: boolean
  busy: boolean
  onComplete: () => void
}) {
  if (completed) {
    return (
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-success/50 bg-success/10 text-success">
        <motion.svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none">
          <motion.path
            d="M3 8.5L6.5 12L13 4.5"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
          />
        </motion.svg>
      </span>
    )
  }
  return (
    <button
      onClick={onComplete}
      disabled={busy}
      aria-label="Mark complete"
      title="Mark complete"
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-border text-transparent transition-colors hover:border-success/60 hover:text-success/60 disabled:opacity-50"
    >
      <Check className="h-3.5 w-3.5" />
    </button>
  )
}

export function TasksPage() {
  const { tasks, loading, error, filters, setFilters, create, edit, complete, remove } = useTasks()
  const { show } = useToast()

  const [form, setForm] = useState<TaskFormValues>(EMPTY_FORM)
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<TaskFormValues>(EMPTY_FORM)
  const [editError, setEditError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  // Toast-based confirmation replacing `window.confirm()` (§5) -- holds the id pending
  // deletion so the dialog can render/dismiss independently of `busyId`.
  const [pendingDeleteId, setPendingDeleteId] = useState<number | null>(null)

  function updateFilter<K extends keyof TaskFilters>(key: K, value: TaskFilters[K] | undefined) {
    setFilters({ ...filters, [key]: value })
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault()
    const title = form.title.trim()
    if (!title) {
      setFormError('A task needs a title.')
      return
    }
    setCreating(true)
    setFormError(null)
    try {
      await create({
        title,
        due: form.due.trim() || null,
        category: form.category.trim() || null,
        priority: form.priority,
      })
      setForm(EMPTY_FORM)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to create task.')
    } finally {
      setCreating(false)
    }
  }

  function startEdit(task: Task) {
    setEditingId(task.id)
    setEditError(null)
    setEditForm({
      title: task.title,
      due: task.due ?? '',
      category: task.category ?? '',
      priority: task.priority,
    })
  }

  async function handleSaveEdit(id: number) {
    const title = editForm.title.trim()
    if (!title) {
      setEditError("Title can't be blank.")
      return
    }
    setBusyId(id)
    try {
      await edit(id, {
        title,
        due: editForm.due.trim() || null,
        category: editForm.category.trim() || null,
        priority: editForm.priority,
      })
      setEditingId(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to update task.')
    } finally {
      setBusyId(null)
    }
  }

  async function handleComplete(id: number) {
    setBusyId(id)
    try {
      await complete(id)
    } finally {
      setBusyId(null)
    }
  }

  async function confirmDelete() {
    if (pendingDeleteId === null) return
    const id = pendingDeleteId
    setBusyId(id)
    try {
      await remove(id)
      show('Task deleted.', 'success')
    } finally {
      setBusyId(null)
      setPendingDeleteId(null)
    }
  }

  return (
    <main className="px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <p className="text-sm text-text-muted">Create, filter, and manage tasks.</p>

        <Panel className="mt-6 flex flex-wrap items-end gap-3 p-4">
          <label className="flex flex-col text-xs text-text-muted">
            Status
            <Select
              className="mt-1"
              value={filters.status ?? ''}
              onChange={(e) => updateFilter('status', e.target.value || undefined)}
            >
              <option value="">All</option>
              {STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Category
            <Input
              className="mt-1"
              value={filters.category ?? ''}
              onChange={(e) => updateFilter('category', e.target.value || undefined)}
              placeholder="e.g. work"
            />
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Due after
            <Input
              className="mt-1"
              value={filters.due_after ?? ''}
              onChange={(e) => updateFilter('due_after', e.target.value || undefined)}
              placeholder="today"
            />
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Due before
            <Input
              className="mt-1"
              value={filters.due_before ?? ''}
              onChange={(e) => updateFilter('due_before', e.target.value || undefined)}
              placeholder="next friday"
            />
          </label>
          <label className="flex items-center gap-2 pb-1.5 text-xs text-text-muted">
            <input
              type="checkbox"
              checked={filters.overdue_only ?? false}
              onChange={(e) => updateFilter('overdue_only', e.target.checked || undefined)}
              className="h-3.5 w-3.5 accent-primary"
            />
            Overdue only
          </label>
        </Panel>

        <form
          onSubmit={handleCreate}
          className="mt-4 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface/70 p-4 backdrop-blur-md"
        >
          <label className="flex flex-col text-xs text-text-muted">
            Title
            <Input
              className="mt-1 w-56"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Buy milk"
            />
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Due
            <Input
              className="mt-1"
              value={form.due}
              onChange={(e) => setForm({ ...form, due: e.target.value })}
              placeholder="tomorrow at 8pm"
            />
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Category
            <Input
              className="mt-1"
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
            />
          </label>
          <label className="flex flex-col text-xs text-text-muted">
            Priority
            <Select
              className="mt-1"
              value={form.priority}
              onChange={(e) => setForm({ ...form, priority: e.target.value })}
            >
              {PRIORITY_OPTIONS.map((priority) => (
                <option key={priority} value={priority}>
                  {priority}
                </option>
              ))}
            </Select>
          </label>
          <Button type="submit" disabled={creating} loading={creating}>
            Add task
          </Button>
          {formError && <span className="text-sm text-danger">{formError}</span>}
        </form>

        <div className="mt-6 space-y-2">
          {/* Skeleton loaders (§5) instead of a blank list while tasks fetch --
              shaped like a task row (checkbox affix + title/meta lines). */}
          {loading && (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <Panel key={i} className="flex items-center gap-3 p-4">
                  <Skeleton className="h-6 w-6 shrink-0 rounded-full" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-1/3" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </Panel>
              ))}
            </div>
          )}
          {error && <p className="text-danger">Failed to load tasks: {error}</p>}

          {/* Empty-state illustration (§5) instead of a bare "no tasks" line. */}
          {!loading && !error && tasks.length === 0 && (
            <Panel className="flex flex-col items-center gap-2 border-dashed p-10 text-center">
              <ListChecks className="h-8 w-8 text-text-muted" />
              <p className="text-sm text-text-muted">No tasks match those filters.</p>
            </Panel>
          )}

          {/* Staggered fade/slide-in on load (§5). */}
          <StaggerList className="space-y-2">
            {tasks.map((task) => (
              <StaggerItem key={task.id}>
                <Panel
                  className={cn(
                    'p-4 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg',
                    task.overdue && 'border-danger/50 bg-danger/5',
                  )}
                >
                  {editingId === task.id ? (
                    <div className="flex flex-wrap items-end gap-3">
                      <Input
                        value={editForm.title}
                        onChange={(e) => setEditForm({ ...editForm, title: e.target.value })}
                        placeholder="title"
                      />
                      <Input
                        value={editForm.due}
                        onChange={(e) => setEditForm({ ...editForm, due: e.target.value })}
                        placeholder="due"
                      />
                      <Input
                        value={editForm.category}
                        onChange={(e) => setEditForm({ ...editForm, category: e.target.value })}
                        placeholder="category"
                      />
                      <div className="w-32">
                        <Select
                          value={editForm.priority}
                          onChange={(e) => setEditForm({ ...editForm, priority: e.target.value })}
                        >
                          {PRIORITY_OPTIONS.map((priority) => (
                            <option key={priority} value={priority}>
                              {priority}
                            </option>
                          ))}
                        </Select>
                      </div>
                      <Button onClick={() => handleSaveEdit(task.id)} disabled={busyId === task.id} loading={busyId === task.id}>
                        Save
                      </Button>
                      <Button variant="ghost" onClick={() => setEditingId(null)}>
                        Cancel
                      </Button>
                      {editError && <span className="text-sm text-danger">{editError}</span>}
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex items-start gap-3">
                        <CompleteCheckbox
                          completed={task.status === 'completed'}
                          busy={busyId === task.id}
                          onComplete={() => handleComplete(task.id)}
                        />
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={cn(
                                'font-medium text-text',
                                task.status === 'completed' && 'text-text-muted line-through',
                              )}
                            >
                              {task.title}
                            </span>
                            {task.overdue && <Badge tone="danger">Overdue</Badge>}
                            <Badge tone="neutral">{task.priority}</Badge>
                          </div>
                          <div className="mt-1 text-xs text-text-muted">
                            {task.category ? `${task.category} · ` : ''}
                            Due {formatDue(task.due)} · {task.status}
                          </div>
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button variant="ghost" onClick={() => startEdit(task)}>
                          Edit
                        </Button>
                        <Button
                          variant="destructive"
                          onClick={() => setPendingDeleteId(task.id)}
                          disabled={busyId === task.id}
                          loading={busyId === task.id}
                        >
                          Delete
                        </Button>
                      </div>
                    </div>
                  )}
                </Panel>
              </StaggerItem>
            ))}
          </StaggerList>
        </div>
      </div>

      <ConfirmDialog
        open={pendingDeleteId !== null}
        message="Delete this task? This cannot be undone."
        confirmLabel="Delete"
        confirmVariant="destructive"
        loading={busyId === pendingDeleteId}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDeleteId(null)}
      />
    </main>
  )
}
