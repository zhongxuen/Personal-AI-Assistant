import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTasks } from '../hooks/useTasks'
import type { Task, TaskFilters } from '../types/task'

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

function fieldClass(extra = ''): string {
  return `mt-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-sm text-slate-100 ${extra}`
}

export function TasksPage() {
  const { tasks, loading, error, filters, setFilters, create, edit, complete, remove } = useTasks()

  const [form, setForm] = useState<TaskFormValues>(EMPTY_FORM)
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<TaskFormValues>(EMPTY_FORM)
  const [editError, setEditError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

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

  async function handleDelete(id: number) {
    if (!window.confirm('Delete this task? This cannot be undone.')) return
    setBusyId(id)
    try {
      await remove(id)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-4xl">
        <h1 className="text-2xl font-semibold">Tasks</h1>
        <p className="mt-1 text-sm text-slate-400">Create, filter, and manage tasks.</p>

        <div className="mt-6 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4">
          <label className="flex flex-col text-xs text-slate-400">
            Status
            <select
              className={fieldClass()}
              value={filters.status ?? ''}
              onChange={(e) => updateFilter('status', e.target.value || undefined)}
            >
              <option value="">All</option>
              {STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs text-slate-400">
            Category
            <input
              className={fieldClass()}
              value={filters.category ?? ''}
              onChange={(e) => updateFilter('category', e.target.value || undefined)}
              placeholder="e.g. work"
            />
          </label>
          <label className="flex flex-col text-xs text-slate-400">
            Due after
            <input
              className={fieldClass()}
              value={filters.due_after ?? ''}
              onChange={(e) => updateFilter('due_after', e.target.value || undefined)}
              placeholder="today"
            />
          </label>
          <label className="flex flex-col text-xs text-slate-400">
            Due before
            <input
              className={fieldClass()}
              value={filters.due_before ?? ''}
              onChange={(e) => updateFilter('due_before', e.target.value || undefined)}
              placeholder="next friday"
            />
          </label>
          <label className="flex items-center gap-2 pb-1 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={filters.overdue_only ?? false}
              onChange={(e) => updateFilter('overdue_only', e.target.checked || undefined)}
            />
            Overdue only
          </label>
        </div>

        <form
          onSubmit={handleCreate}
          className="mt-4 flex flex-wrap items-end gap-3 rounded-lg border border-slate-800 bg-slate-900 p-4"
        >
          <label className="flex flex-col text-xs text-slate-400">
            Title
            <input
              className={fieldClass('w-56')}
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              placeholder="Buy milk"
            />
          </label>
          <label className="flex flex-col text-xs text-slate-400">
            Due
            <input
              className={fieldClass()}
              value={form.due}
              onChange={(e) => setForm({ ...form, due: e.target.value })}
              placeholder="tomorrow at 8pm"
            />
          </label>
          <label className="flex flex-col text-xs text-slate-400">
            Category
            <input
              className={fieldClass()}
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
            />
          </label>
          <label className="flex flex-col text-xs text-slate-400">
            Priority
            <select
              className={fieldClass()}
              value={form.priority}
              onChange={(e) => setForm({ ...form, priority: e.target.value })}
            >
              {PRIORITY_OPTIONS.map((priority) => (
                <option key={priority} value={priority}>
                  {priority}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={creating}
            className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {creating ? 'Adding…' : 'Add task'}
          </button>
          {formError && <span className="text-sm text-red-400">{formError}</span>}
        </form>

        <div className="mt-6 space-y-2">
          {loading && <p className="text-slate-400">Loading tasks…</p>}
          {error && <p className="text-red-400">Failed to load tasks: {error}</p>}
          {!loading && !error && tasks.length === 0 && (
            <p className="text-slate-400">No tasks match those filters.</p>
          )}

          {tasks.map((task) => (
            <div
              key={task.id}
              className={`rounded-lg border p-4 ${
                task.overdue ? 'border-red-900 bg-red-950/40' : 'border-slate-800 bg-slate-900'
              }`}
            >
              {editingId === task.id ? (
                <div className="flex flex-wrap items-end gap-3">
                  <input
                    className={fieldClass('mt-0')}
                    value={editForm.title}
                    onChange={(e) => setEditForm({ ...editForm, title: e.target.value })}
                    placeholder="title"
                  />
                  <input
                    className={fieldClass('mt-0')}
                    value={editForm.due}
                    onChange={(e) => setEditForm({ ...editForm, due: e.target.value })}
                    placeholder="due"
                  />
                  <input
                    className={fieldClass('mt-0')}
                    value={editForm.category}
                    onChange={(e) => setEditForm({ ...editForm, category: e.target.value })}
                    placeholder="category"
                  />
                  <select
                    className={fieldClass('mt-0')}
                    value={editForm.priority}
                    onChange={(e) => setEditForm({ ...editForm, priority: e.target.value })}
                  >
                    {PRIORITY_OPTIONS.map((priority) => (
                      <option key={priority} value={priority}>
                        {priority}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => handleSaveEdit(task.id)}
                    disabled={busyId === task.id}
                    className="rounded bg-emerald-600 px-3 py-1 text-sm text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditingId(null)}
                    className="rounded border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:bg-slate-800"
                  >
                    Cancel
                  </button>
                  {editError && <span className="text-sm text-red-400">{editError}</span>}
                </div>
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`font-medium ${
                          task.status === 'completed' ? 'text-slate-500 line-through' : ''
                        }`}
                      >
                        {task.title}
                      </span>
                      {task.overdue && (
                        <span className="rounded bg-red-600/80 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-white">
                          Overdue
                        </span>
                      )}
                      <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
                        {task.priority}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-400">
                      {task.category ? `${task.category} · ` : ''}
                      Due {formatDue(task.due)} · {task.status}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {task.status !== 'completed' && (
                      <button
                        onClick={() => handleComplete(task.id)}
                        disabled={busyId === task.id}
                        className="rounded border border-emerald-700 px-3 py-1 text-xs text-emerald-400 hover:bg-emerald-900/40 disabled:opacity-50"
                      >
                        Complete
                      </button>
                    )}
                    <button
                      onClick={() => startEdit(task)}
                      className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(task.id)}
                      disabled={busyId === task.id}
                      className="rounded border border-red-800 px-3 py-1 text-xs text-red-400 hover:bg-red-900/40 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </main>
  )
}
