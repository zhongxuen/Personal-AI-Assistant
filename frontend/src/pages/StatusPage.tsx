import { useHealth } from '../hooks/useHealth'
import { Panel } from '../components/ui'
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

export function StatusPage() {
  const health = useHealth()

  return (
    <main className="flex min-h-full items-center justify-center px-6 py-10">
      <Panel className="p-8 shadow-xl">
        <h1 className="font-display text-2xl font-semibold text-text">JARVIS</h1>
        <p className="mt-1 text-sm text-text-muted">Backend connectivity check</p>

        <div className="mt-6 flex items-center gap-3">
          <HeartbeatDot
            tone={health.status === 'ready' ? 'ready' : health.status === 'error' ? 'error' : 'loading'}
          />
          {health.status === 'loading' && <span className="text-text-muted">Checking backend…</span>}
          {health.status === 'error' && (
            <span className="text-danger">Backend unreachable: {health.message}</span>
          )}
          {health.status === 'ready' && (
            <span className="text-success">
              {health.data.service} — {health.data.status}
            </span>
          )}
        </div>
      </Panel>
    </main>
  )
}
