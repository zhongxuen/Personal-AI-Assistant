import { useState } from 'react'
import { CheckCircle2, ListChecks, PlayCircle, XCircle } from 'lucide-react'
import { useHealth } from '../hooks/useHealth'
import { useDiagnostics } from '../hooks/useDiagnostics'
import { Badge, Button, Panel, Skeleton, StaggerItem, StaggerList } from '../components/ui'
import { cn } from '../components/ui/utils'

/** Heartbeat/pulse indicator tied to the health-check result (§5) -- an expanding
 * `animate-ping` ring only while the backend is confirmed healthy (echoing the
 * "signature interaction" pulse VoiceControl uses while recording), a steady dim dot
 * while the first check is still in flight, and a solid, non-pulsing dot on error so a
 * failed check doesn't read as "still alive and pulsing".
 */
function HeartbeatDot({ tone }: { tone: 'loading' | 'error' | 'ready' }) {
  return (
    <span className="relative flex h-3 w-3 shrink-0">
      {tone === 'ready' && (
        <span className="absolute inset-0 animate-ping rounded-full bg-success/60" aria-hidden="true" />
      )}
      <span
        className={cn(
          'relative inline-flex h-3 w-3 rounded-full',
          tone === 'ready' && 'bg-success',
          tone === 'error' && 'bg-danger',
          tone === 'loading' && 'animate-pulse bg-text-muted',
        )}
      />
    </span>
  )
}

/** "Run the whole system" self-test: runs every backend component's health check (or
 * just the ones the operator selects) and reports pass/fail per component, so a broken
 * provider/service can be spotted by name instead of guessed from backend logs.
 * Backed by `GET/POST /api/diagnostics/*` (`app/diagnostics/service.py`).
 */
function DiagnosticsPanel() {
  const { checks, loadingChecks, checksError, running, runError, result, run } = useDiagnostics()
  // Tracks which components have been explicitly unchecked -- everything else is
  // selected by default (derived below), so the default "Run system test" click tests
  // everything without needing an effect to seed a full-selection Set once the
  // catalog loads. Unchecking a box narrows the next run down to a specific suspect
  // (§ "maybe can customize if needed that what command isn't working").
  const [deselected, setDeselected] = useState<Set<string>>(new Set())
  const selected = new Set(checks.map((c) => c.name).filter((name) => !deselected.has(name)))

  function toggle(name: string) {
    setDeselected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const allSelected = checks.length > 0 && selected.size === checks.length
  const failedCount = result?.results.filter((r) => !r.ok).length ?? 0

  return (
    <Panel className="mt-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-text-muted" />
          <h2 className="font-display text-sm font-semibold text-text">System diagnostics</h2>
        </div>
        <Button
          onClick={() => run([...selected])}
          disabled={running || selected.size === 0}
          loading={running}
          className="w-full sm:w-auto"
        >
          <PlayCircle className="h-3.5 w-3.5" />
          Run system test
        </Button>
      </div>
      <p className="mt-1 text-xs text-text-muted">
        Runs a read-only check against every component below -- database, LLM providers, voice,
        Discord, routines -- and reports which ones are actually broken. Nothing here sends a
        message, opens an app, or has any other side effect.
      </p>

      {loadingChecks && (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-5 w-full" />
          ))}
        </div>
      )}
      {checksError && <p className="mt-4 text-sm text-danger">Failed to load checks: {checksError}</p>}

      {!loadingChecks && !checksError && checks.length > 0 && (
        <>
          <div className="mt-4 flex items-center justify-between">
            <span className="text-xs text-text-muted">Components to test</span>
            <button
              type="button"
              onClick={() => setDeselected(allSelected ? new Set(checks.map((c) => c.name)) : new Set())}
              className="text-xs text-primary hover:underline"
            >
              {allSelected ? 'Select none' : 'Select all'}
            </button>
          </div>
          <div className="mt-2 grid gap-x-4 gap-y-2 sm:grid-cols-2">
            {checks.map((check) => {
              const resultForCheck = result?.results.find((r) => r.name === check.name)
              return (
                <label
                  key={check.name}
                  className="flex items-center gap-2 rounded-md border border-border bg-surface-2/40 px-2.5 py-1.5 text-sm text-text"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(check.name)}
                    onChange={() => toggle(check.name)}
                    className="h-3.5 w-3.5 accent-primary"
                  />
                  <span className="flex-1 truncate">{check.label}</span>
                  {resultForCheck &&
                    (resultForCheck.ok ? (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" aria-label="Passed" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5 shrink-0 text-danger" aria-label="Failed" />
                    ))}
                </label>
              )
            })}
          </div>
        </>
      )}

      {runError && <p className="mt-4 text-sm text-danger">{runError}</p>}

      {result && (
        <div className="mt-5 border-t border-border pt-4">
          <div className="flex items-center gap-2">
            <Badge tone={result.ok ? 'success' : 'danger'}>
              {result.ok ? 'All clear' : `${failedCount} of ${result.results.length} failed`}
            </Badge>
          </div>
          {/* Staggered fade/slide-in on load (§5), same as Routines/ProviderStatus. */}
          <StaggerList className="mt-3 space-y-2">
            {result.results.map((r) => (
              <StaggerItem key={r.name}>
                <div
                  className={cn(
                    'rounded-md border p-3 text-sm',
                    r.ok ? 'border-success/40 bg-success/5' : 'border-danger/50 bg-danger/10',
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="min-w-0 break-words font-medium text-text">{r.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-text-muted">{r.duration_ms}ms</span>
                      <Badge tone={r.ok ? 'success' : 'danger'}>{r.ok ? 'OK' : 'FAIL'}</Badge>
                    </div>
                  </div>
                  <p className={cn('mt-1 break-words text-xs', r.ok ? 'text-text-muted' : 'text-danger')}>
                    {r.message}
                  </p>
                </div>
              </StaggerItem>
            ))}
          </StaggerList>
        </div>
      )}
    </Panel>
  )
}

export function StatusPage() {
  const health = useHealth()

  return (
    <main className="px-4 pb-28 pt-6 sm:px-6 sm:pb-10 sm:pt-10">
      <div className="mx-auto max-w-3xl">
        <Panel className="p-6 shadow-xl sm:p-8">
          <h1 className="font-display text-2xl font-semibold text-text">JARVIS</h1>
          <p className="mt-1 text-sm text-text-muted">Backend connectivity check</p>

          <div className="mt-6 flex items-start gap-3">
            <HeartbeatDot
              tone={health.status === 'ready' ? 'ready' : health.status === 'error' ? 'error' : 'loading'}
            />
            {health.status === 'loading' && <span className="text-text-muted">Checking backend…</span>}
            {health.status === 'error' && (
              <span className="min-w-0 break-words text-danger">Backend unreachable: {health.message}</span>
            )}
            {health.status === 'ready' && (
              <span className="text-success">
                {health.data.service} — {health.data.status}
              </span>
            )}
          </div>
        </Panel>

        <DiagnosticsPanel />
      </div>
    </main>
  )
}
