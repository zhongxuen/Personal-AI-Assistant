import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMemorySettings } from '../hooks/useMemorySettings'
import type { ApplicationMapping } from '../types/memory'
import { Button, ConfirmDialog, Input, Panel, Skeleton, StaggerItem, StaggerList, useToast } from '../components/ui'

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
  const { show } = useToast()

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

  // Toast-based confirmation replacing `window.confirm()` (§5).
  const [pendingDeleteAlias, setPendingDeleteAlias] = useState<string | null>(null)
  const [deletingAlias, setDeletingAlias] = useState(false)

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
      // Inline save confirmation via toast instead of silent success (§5).
      show(`Saved '${alias}'.`, 'success')
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to save mapping.')
    } finally {
      setSaving(false)
    }
  }

  async function confirmDelete() {
    if (!pendingDeleteAlias) return
    const alias = pendingDeleteAlias
    setDeletingAlias(true)
    try {
      await removeApplication(alias)
      if (editingAlias === alias) setEditingAlias(null)
      show(`Removed '${alias}'.`, 'success')
    } finally {
      setDeletingAlias(false)
      setPendingDeleteAlias(null)
    }
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
      show(`Added '${alias}'.`, 'success')
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
      show('Default project saved.', 'success')
    } catch (err) {
      setProjectError(err instanceof Error ? err.message : 'Failed to save default project.')
    } finally {
      setSavingProject(false)
    }
  }

  return (
    <main className="px-6 py-10">
      <div className="mx-auto max-w-3xl">
        <p className="text-sm text-text-muted">
          Memory-backed application mappings and the "coding" routine's default project -- the
          same values <code className="text-text">open_application</code> and{' '}
          <code className="text-text">run_routine</code> resolve against.
        </p>

        {/* Skeleton loaders (§5) instead of a blank page while settings fetch --
            shaped like the default-project panel plus a couple of mapping cards. */}
        {loading && (
          <div className="mt-8 space-y-3">
            <Panel className="p-4">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="mt-3 h-9 w-56" />
            </Panel>
            {[0, 1].map((i) => (
              <Panel key={i} className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-16" />
                </div>
              </Panel>
            ))}
          </div>
        )}
        {error && <p className="mt-6 text-danger">Failed to load settings: {error}</p>}

        {!loading && !error && (
          <>
            {/* Grouped panel layout (§5) -- one Panel per logical settings group. */}
            <Panel className="mt-8 p-4">
              <h2 className="font-display text-sm font-semibold text-text">Default project</h2>
              <p className="mt-1 text-xs text-text-muted">
                The application alias "Start coding" opens alongside your editor and browser.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Input
                  className="w-56"
                  value={projectDraft ?? defaultProject}
                  onChange={(e) => setProjectDraft(e.target.value)}
                  placeholder="portfolio"
                />
                <Button
                  onClick={handleSaveProject}
                  disabled={savingProject || (projectDraft ?? defaultProject) === defaultProject}
                  loading={savingProject}
                >
                  Save
                </Button>
              </div>
              {projectError && <p className="mt-2 text-sm text-danger">{projectError}</p>}
            </Panel>

            <section className="mt-6 space-y-3">
              <h2 className="font-display text-sm font-semibold text-text">Application mappings</h2>
              {aliases.length === 0 && (
                <p className="text-sm text-text-muted">No application mappings yet -- add one below.</p>
              )}
              {/* Staggered fade/slide-in on load (§5). */}
              <StaggerList className="space-y-3">
              {aliases.map((alias) => {
                const mapping = applications[alias]
                const isEditing = editingAlias === alias
                return (
                  <StaggerItem key={alias}>
                  <Panel className="p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span className="font-medium text-text">{alias}</span>
                      <div className="flex gap-2">
                        {isEditing ? (
                          <Button variant="ghost" onClick={() => setEditingAlias(null)}>
                            Cancel
                          </Button>
                        ) : (
                          <Button variant="ghost" onClick={() => startEdit(alias)}>
                            Edit
                          </Button>
                        )}
                        <Button variant="destructive" onClick={() => setPendingDeleteAlias(alias)}>
                          Delete
                        </Button>
                      </div>
                    </div>

                    {isEditing ? (
                      <div className="mt-3 space-y-2 border-t border-border pt-3">
                        <label className="block text-xs text-text-muted">
                          Command (comma-separated)
                          <Input
                            className="mt-1 w-full font-mono text-xs"
                            value={editValue.commandText}
                            onChange={(e) => setEditValue({ ...editValue, commandText: e.target.value })}
                          />
                        </label>
                        <label className="block text-xs text-text-muted">
                          Process names (comma-separated)
                          <Input
                            className="mt-1 w-full font-mono text-xs"
                            value={editValue.processNamesText}
                            onChange={(e) =>
                              setEditValue({ ...editValue, processNamesText: e.target.value })
                            }
                          />
                        </label>
                        {editError && <p className="text-sm text-danger">{editError}</p>}
                        <Button onClick={() => handleSaveEdit(alias)} disabled={saving} loading={saving}>
                          Save
                        </Button>
                      </div>
                    ) : (
                      <p className="mt-2 text-xs text-text-muted">
                        command: <span className="font-mono text-text">{mapping.command.join(', ')}</span>
                        {mapping.process_names.length > 0 && (
                          <>
                            {' '}
                            · process names:{' '}
                            <span className="font-mono text-text">{mapping.process_names.join(', ')}</span>
                          </>
                        )}
                      </p>
                    )}
                  </Panel>
                  </StaggerItem>
                )
              })}
              </StaggerList>
            </section>

            <form onSubmit={handleCreate}>
              <Panel className="mt-6 p-4">
                <h2 className="font-display text-sm font-semibold text-text">New application mapping</h2>
                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  <Input
                    value={newAlias}
                    onChange={(e) => setNewAlias(e.target.value)}
                    placeholder="alias (e.g. slack)"
                  />
                  <Input
                    className="font-mono text-xs"
                    value={newValue.commandText}
                    onChange={(e) => setNewValue({ ...newValue, commandText: e.target.value })}
                    placeholder="command (comma-separated)"
                  />
                  <Input
                    className="font-mono text-xs"
                    value={newValue.processNamesText}
                    onChange={(e) => setNewValue({ ...newValue, processNamesText: e.target.value })}
                    placeholder="process names (comma-separated)"
                  />
                </div>
                {createError && <p className="mt-2 text-sm text-danger">{createError}</p>}
                <Button type="submit" disabled={creating} loading={creating} className="mt-3">
                  Add mapping
                </Button>
              </Panel>
            </form>
          </>
        )}
      </div>

      <ConfirmDialog
        open={pendingDeleteAlias !== null}
        message={`Remove the '${pendingDeleteAlias}' application mapping?`}
        confirmLabel="Delete"
        confirmVariant="destructive"
        loading={deletingAlias}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDeleteAlias(null)}
      />
    </main>
  )
}
