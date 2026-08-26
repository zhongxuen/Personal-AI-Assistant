import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMemorySettings } from '../hooks/useMemorySettings'
import type { ApplicationMapping } from '../types/memory'

/** An application mapping mid-edit: command/process_names are kept as raw
 * comma-separated text so the inputs don't fight the user while typing, and parsed
 * only when the mapping is saved -- same "raw text, parse on save" pattern
 * Routines.tsx's step editor uses for step params.
 */
interface EditableMapping {
  commandText: string
  processNamesText: string
}

function toEditable(mapping: ApplicationMapping): EditableMapping {
  return {
    commandText: mapping.command.join(', '),
    processNamesText: mapping.process_names.join(', '),
  }
}

function toMapping(editable: EditableMapping): ApplicationMapping {
  return {
    command: editable.commandText
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean),
    process_names: editable.processNamesText
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean),
  }
}

const EMPTY_EDITABLE: EditableMapping = { commandText: '', processNamesText: '' }

export function SettingsPage() {
  const {
    applications,
    defaultProject,
    loading,
    error,
    saveApplication,
    removeApplication,
    saveDefaultProject,
  } = useMemorySettings()

  const [editingAlias, setEditingAlias] = useState<string | null>(null)
  const [editValue, setEditValue] = useState<EditableMapping>(EMPTY_EDITABLE)
  const [editError, setEditError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [newAlias, setNewAlias] = useState('')
  const [newValue, setNewValue] = useState<EditableMapping>(EMPTY_EDITABLE)
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const [projectDraft, setProjectDraft] = useState<string | null>(null)
  const [projectError, setProjectError] = useState<string | null>(null)
  const [savingProject, setSavingProject] = useState(false)

  const aliases = Object.keys(applications).sort()

  function startEdit(alias: string) {
    setEditingAlias(alias)
    setEditValue(toEditable(applications[alias]))
    setEditError(null)
  }

  async function handleSaveEdit(alias: string) {
    const mapping = toMapping(editValue)
    if (mapping.command.length === 0) {
      setEditError('A mapping needs at least one command.')
      return
    }
    setSaving(true)
    setEditError(null)
    try {
      await saveApplication(alias, mapping)
      setEditingAlias(null)
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to save mapping.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(alias: string) {
    if (!window.confirm(`Remove the '${alias}' application mapping?`)) return
    await removeApplication(alias)
    if (editingAlias === alias) setEditingAlias(null)
  }

  async function handleCreate(event: FormEvent) {
    event.preventDefault()
    const alias = newAlias.trim()
    if (!alias) {
      setCreateError('An alias is required.')
      return
    }
    const mapping = toMapping(newValue)
    if (mapping.command.length === 0) {
      setCreateError('A mapping needs at least one command.')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      await saveApplication(alias, mapping)
      setNewAlias('')
      setNewValue(EMPTY_EDITABLE)
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create mapping.')
    } finally {
      setCreating(false)
    }
  }

  async function handleSaveProject() {
    const value = (projectDraft ?? defaultProject).trim()
    if (!value) {
      setProjectError('Default project cannot be blank.')
      return
    }
    setSavingProject(true)
    setProjectError(null)
    try {
      await saveDefaultProject(value)
      setProjectDraft(null)
    } catch (err) {
      setProjectError(err instanceof Error ? err.message : 'Failed to save default project.')
    } finally {
      setSavingProject(false)
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-3xl">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-slate-400">
          Memory-backed application mappings and the "coding" routine's default project --
          the same values <code className="text-slate-300">open_application</code> and{' '}
          <code className="text-slate-300">run_routine</code> resolve against.
        </p>

        {loading && <p className="mt-6 text-slate-400">Loading settings…</p>}
        {error && <p className="mt-6 text-red-400">Failed to load settings: {error}</p>}

        {!loading && !error && (
          <>
            <section className="mt-8 rounded-lg border border-slate-800 bg-slate-900 p-4">
              <h2 className="text-sm font-semibold text-slate-200">Default project</h2>
              <p className="mt-1 text-xs text-slate-500">
                The application alias "Start coding" opens alongside your editor and browser.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <input
                  className="w-56 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-sm text-slate-100"
                  value={projectDraft ?? defaultProject}
                  onChange={(e) => setProjectDraft(e.target.value)}
                  placeholder="portfolio"
                />
                <button
                  onClick={handleSaveProject}
                  disabled={savingProject || (projectDraft ?? defaultProject) === defaultProject}
                  className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  {savingProject ? 'Saving…' : 'Save'}
                </button>
              </div>
              {projectError && <p className="mt-2 text-sm text-red-400">{projectError}</p>}
            </section>

            <section className="mt-6 space-y-3">
              <h2 className="text-sm font-semibold text-slate-200">Application mappings</h2>
              {aliases.length === 0 && (
                <p className="text-sm text-slate-400">No application mappings yet -- add one below.</p>
              )}
              {aliases.map((alias) => {
                const mapping = applications[alias]
                const isEditing = editingAlias === alias
                return (
                  <div key={alias} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className="font-medium">{alias}</span>
                      <div className="flex gap-2">
                        {isEditing ? (
                          <button
                            onClick={() => setEditingAlias(null)}
                            className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                          >
                            Cancel
                          </button>
                        ) : (
                          <button
                            onClick={() => startEdit(alias)}
                            className="rounded border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                          >
                            Edit
                          </button>
                        )}
                        <button
                          onClick={() => handleDelete(alias)}
                          className="rounded border border-red-800 px-3 py-1 text-xs text-red-400 hover:bg-red-900/40"
                        >
                          Delete
                        </button>
                      </div>
                    </div>

                    {isEditing ? (
                      <div className="mt-3 space-y-2 border-t border-slate-800 pt-3">
                        <label className="block text-xs text-slate-500">
                          Command (comma-separated)
                          <input
                            className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 font-mono text-xs text-slate-100"
                            value={editValue.commandText}
                            onChange={(e) => setEditValue({ ...editValue, commandText: e.target.value })}
                          />
                        </label>
                        <label className="block text-xs text-slate-500">
                          Process names (comma-separated)
                          <input
                            className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-2 py-1 font-mono text-xs text-slate-100"
                            value={editValue.processNamesText}
                            onChange={(e) =>
                              setEditValue({ ...editValue, processNamesText: e.target.value })
                            }
                          />
                        </label>
                        {editError && <p className="text-sm text-red-400">{editError}</p>}
                        <button
                          onClick={() => handleSaveEdit(alias)}
                          disabled={saving}
                          className="rounded bg-emerald-600 px-3 py-1 text-sm text-white hover:bg-emerald-500 disabled:opacity-50"
                        >
                          {saving ? 'Saving…' : 'Save'}
                        </button>
                      </div>
                    ) : (
                      <p className="mt-2 text-xs text-slate-400">
                        command: <span className="font-mono text-slate-300">{mapping.command.join(', ')}</span>
                        {mapping.process_names.length > 0 && (
                          <>
                            {' '}
                            · process names:{' '}
                            <span className="font-mono text-slate-300">{mapping.process_names.join(', ')}</span>
                          </>
                        )}
                      </p>
                    )}
                  </div>
                )
              })}
            </section>

            <form onSubmit={handleCreate} className="mt-6 rounded-lg border border-slate-800 bg-slate-900 p-4">
              <h2 className="text-sm font-semibold text-slate-200">New application mapping</h2>
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <input
                  className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-sm text-slate-100"
                  value={newAlias}
                  onChange={(e) => setNewAlias(e.target.value)}
                  placeholder="alias (e.g. slack)"
                />
                <input
                  className="rounded border border-slate-700 bg-slate-800 px-2 py-1 font-mono text-xs text-slate-100"
                  value={newValue.commandText}
                  onChange={(e) => setNewValue({ ...newValue, commandText: e.target.value })}
                  placeholder="command (comma-separated)"
                />
                <input
                  className="rounded border border-slate-700 bg-slate-800 px-2 py-1 font-mono text-xs text-slate-100"
                  value={newValue.processNamesText}
                  onChange={(e) => setNewValue({ ...newValue, processNamesText: e.target.value })}
                  placeholder="process names (comma-separated)"
                />
              </div>
              {createError && <p className="mt-2 text-sm text-red-400">{createError}</p>}
              <button
                type="submit"
                disabled={creating}
                className="mt-3 rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {creating ? 'Creating…' : 'Add mapping'}
              </button>
            </form>
          </>
        )}
      </div>
    </main>
  )
}
