import { useState } from 'react'
import type { FormEvent } from 'react'
import { Bot, FolderTree, Pause, Play, Trash2 } from 'lucide-react'
import { useMemorySettings } from '../hooks/useMemorySettings'
import { useDiscordBot } from '../hooks/useDiscordBot'
import { useProjects } from '../hooks/useProjects'
import type { ApplicationMapping } from '../types/memory'
import type { DiscordBotState } from '../types/discord'
import {
  Badge,
  Button,
  ConfirmDialog,
  Input,
  Panel,
  Skeleton,
  StaggerItem,
  StaggerList,
  useToast,
} from '../components/ui'
import type { BadgeTone } from '../components/ui'

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

// Same tone/label mapping approach as ProviderStatus.tsx's PROVIDER_STATUS_TONE --
// "connected" reads as success, "error" as danger, "starting" as an in-progress
// warning, "stopped"/"disabled" as neutral (nothing wrong, just not running).
const DISCORD_STATE_TONE: Record<DiscordBotState, BadgeTone> = {
  disabled: 'neutral',
  stopped: 'neutral',
  starting: 'warning',
  connected: 'success',
  error: 'danger',
}

const DISCORD_STATE_LABEL: Record<DiscordBotState, string> = {
  disabled: 'Not configured',
  stopped: 'Stopped',
  starting: 'Starting…',
  connected: 'Connected',
  error: 'Error',
}

/** Discord bot start/stop control -- the web-dashboard replacement for having to run
 * scripts/start-discord-bot.ps1 locally. Talks to whatever backend this frontend is
 * pointed at (local dev or the deployed Render instance) via `useDiscordBot`, which
 * wraps `app.api.routes.discord`/`DiscordBotManager`.
 */
function DiscordBotPanel() {
  const { status, loading, error, starting, stopping, start, stop } = useDiscordBot()
  const { show } = useToast()

  async function handleStart() {
    try {
      await start()
      show('Discord bot started.', 'success')
    } catch (err) {
      show(err instanceof Error ? err.message : 'Failed to start the Discord bot.', 'danger')
    }
  }

  async function handleStop() {
    try {
      await stop()
      show('Discord bot stopped.', 'success')
    } catch (err) {
      show(err instanceof Error ? err.message : 'Failed to stop the Discord bot.', 'danger')
    }
  }

  return (
    <Panel className="mt-8 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Bot className="h-4 w-4 text-text-muted" />
          <h2 className="font-display text-sm font-semibold text-text">Discord bot</h2>
        </div>
        {loading ? (
          <Skeleton className="h-5 w-24 rounded" />
        ) : (
          status && <Badge tone={DISCORD_STATE_TONE[status.state]}>{DISCORD_STATE_LABEL[status.state]}</Badge>
        )}
      </div>

      {loading ? (
        <Skeleton className="mt-3 h-9 w-32" />
      ) : !status?.configured ? (
        <p className="mt-2 text-xs text-text-muted">
          <code className="text-text">DISCORD_BOT_TOKEN</code> isn't set on the backend, so there's
          nothing to start yet -- add it to <code className="text-text">.env</code> (local) or the
          Render dashboard's Environment tab (deployed), then reload this page.
        </p>
      ) : (
        <>
          <p className="mt-1 text-xs text-text-muted">
            Talks to and controls whichever backend this page is pointed at -- the same one
            everything else on this dashboard uses.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {status.state === 'connected' || status.state === 'starting' ? (
              <Button variant="ghost" onClick={handleStop} disabled={stopping} loading={stopping}>
                <Pause className="h-3.5 w-3.5" />
                Stop
              </Button>
            ) : (
              <Button onClick={handleStart} disabled={starting} loading={starting}>
                <Play className="h-3.5 w-3.5" />
                Start
              </Button>
            )}
            {status.username && <span className="text-xs text-text-muted">as {status.username}</span>}
          </div>
          {status.state === 'error' && status.error && (
            <p className="mt-2 text-sm text-danger">{status.error}</p>
          )}
        </>
      )}
      {error && <p className="mt-2 text-sm text-danger">Failed to load Discord bot status: {error}</p>}
    </Panel>
  )
}

/** Scan-root editor for the Coding Routine template's project picker
 * (`frontend/src/components/CodingRoutinePanel.tsx` / `GET /api/projects`). Roots are
 * plain folder paths (e.g. the "Coding" folder, or this repo's own temporary "Dev"
 * location -- see `CLAUDE.md`) whose immediate subdirectories become project options;
 * this panel only edits the root list, not individual projects -- those come from
 * whatever's actually on disk under each root.
 */
function ProjectRootsPanel() {
  const { projects, roots, loading, error, saveRoots } = useProjects()
  const { show } = useToast()

  const [newRoot, setNewRoot] = useState('')
  const [saving, setSaving] = useState(false)
  const [rootError, setRootError] = useState<string | null>(null)

  async function persist(nextRoots: string[], successMessage: string) {
    setSaving(true)
    setRootError(null)
    try {
      await saveRoots(nextRoots)
      show(successMessage, 'success')
    } catch (err) {
      setRootError(err instanceof Error ? err.message : 'Failed to save project folders.')
    } finally {
      setSaving(false)
    }
  }

  async function handleAddRoot(event: FormEvent) {
    event.preventDefault()
    const value = newRoot.trim()
    if (!value) {
      setRootError('A folder path is required.')
      return
    }
    if (roots.includes(value)) {
      setRootError('That folder is already in the list.')
      return
    }
    await persist([...roots, value], `Added '${value}'.`)
    setNewRoot('')
  }

  async function handleRemoveRoot(root: string) {
    if (roots.length <= 1) {
      setRootError('At least one project folder is required.')
      return
    }
    await persist(
      roots.filter((r) => r !== root),
      `Removed '${root}'.`,
    )
  }

  return (
    <Panel className="mt-8 p-4">
      <div className="flex items-center gap-2">
        <FolderTree className="h-4 w-4 text-text-muted" />
        <h2 className="font-display text-sm font-semibold text-text">Project folders</h2>
      </div>
      <p className="mt-1 text-xs text-text-muted">
        Folders scanned for the Coding Routine's project picker -- every immediate subfolder shows
        up there as an option.
      </p>

      {loading ? (
        <Skeleton className="mt-3 h-9 w-64" />
      ) : (
        <>
          <div className="mt-3 space-y-2">
            {roots.map((root) => (
              <div
                key={root}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface-2/60 p-2"
              >
                <span className="font-mono text-xs text-text">{root}</span>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => handleRemoveRoot(root)}
                  disabled={saving}
                  aria-label={`Remove ${root}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-text-muted">
            {projects.length} project{projects.length === 1 ? '' : 's'} found: {projects.map((p) => p.name).join(', ') || 'none yet'}
          </p>
          <form onSubmit={handleAddRoot} className="mt-3 flex flex-wrap items-center gap-2">
            <Input
              className="w-64 font-mono text-xs"
              value={newRoot}
              onChange={(e) => setNewRoot(e.target.value)}
              placeholder="C:\Users\you\Coding"
            />
            <Button type="submit" disabled={saving} loading={saving}>
              Add folder
            </Button>
          </form>
          {rootError && <p className="mt-2 text-sm text-danger">{rootError}</p>}
          {error && <p className="mt-2 text-sm text-danger">Failed to load project folders: {error}</p>}
        </>
      )}
    </Panel>
  )
}

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

        {/* Independent of the memory-settings load below -- its own data source
            (`/api/discord/status`), so it renders/loads on its own rather than waiting
            on `loading`/`error` from useMemorySettings. */}
        <DiscordBotPanel />

        {/* Also independent of useMemorySettings -- its own data source
            (`/api/projects`), same reasoning as DiscordBotPanel above. */}
        <ProjectRootsPanel />

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
