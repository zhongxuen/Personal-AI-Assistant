import { useEffect, useRef, useState } from 'react'
import { RefreshCw, RotateCcw } from 'lucide-react'
import type { useLlmUsage } from '../hooks/useLlmUsage'
import { resetProviderHealth } from '../services/api'
import { LimitBar } from '../components/LimitBar'
import type { ProviderStatusBadge, ProviderUsage } from '../types/llmUsage'
import type { BadgeTone } from '../components/ui'
import { Badge, Button, Panel, PROVIDER_STATUS_TONE, Skeleton, StaggerItem, StaggerList } from '../components/ui'
import { cn } from '../components/ui/utils'

// `border-current` ping ring needs an explicit text color per tone to pick up (§5's
// "ping" callout is generic across tones, not just danger/warning).
const PING_COLOR: Record<BadgeTone, string> = {
  primary: 'text-primary',
  secondary: 'text-secondary',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  neutral: 'text-text-muted',
}

/** True for the ~1s right after `status` changes from what it was on the previous
 * render -- drives the "live-updating badge" ping (§5) so a provider flipping into
 * WARNING/CRITICAL/FAILOVER between `useLlmUsage`'s 15s polls draws the eye instead of
 * silently swapping color.
 */
function useJustChanged(status: ProviderStatusBadge): boolean {
  const prev = useRef(status)
  const [justChanged, setJustChanged] = useState(false)

  useEffect(() => {
    if (prev.current === status) return
    prev.current = status
    setJustChanged(true)
    const timeout = window.setTimeout(() => setJustChanged(false), 1000)
    return () => window.clearTimeout(timeout)
  }, [status])

  return justChanged
}

function StatusBadge({ status }: { status: ProviderStatusBadge }) {
  const justChanged = useJustChanged(status)
  const tone = PROVIDER_STATUS_TONE[status]
  return (
    <span className="relative inline-flex">
      {justChanged && (
        <span
          className={cn('absolute inset-0 animate-ping rounded border border-current', PING_COLOR[tone])}
          aria-hidden="true"
        />
      )}
      <Badge tone={tone}>{status}</Badge>
    </span>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-xs text-text-muted">{label}</div>
      <div className="font-mono text-lg font-semibold text-text">{value}</div>
    </div>
  )
}

/** The reset control on an unhealthy provider's card.
 *
 * Only rendered when `health.healthy` is false, because that's the only time it does
 * anything: `HealthManager`'s MISCONFIGURED/DISABLED states are sticky (no cooldown
 * clears them), so a provider benched by one bad API key or one unreachable
 * `GEMINI_MODEL` stays out of `AIRouter`'s chain until the backend restarts -- and
 * every chat meanwhile answers "I can't reach any reasoning provider right now". This
 * button is what turns "fix the env var, then redeploy" into "fix the env var, then
 * click this".
 *
 * It resets bookkeeping only -- nothing is re-configured and the provider isn't
 * called -- so if the root cause is still there, the next request simply puts the
 * provider back into the same state. Hence the `refresh()` on success rather than
 * optimistically painting the card healthy.
 */
function ResetHealthButton({ provider, onDone }: { provider: string; onDone: () => void }) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleClick() {
    setPending(true)
    setError(null)
    try {
      await resetProviderHealth(provider)
      onDone()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-3">
      <Button variant="ghost" loading={pending} onClick={() => void handleClick()}>
        <RotateCcw className="h-4 w-4" />
        Reset health
      </Button>
      <span className="text-xs text-text-muted">
        Puts {provider} back in the chain for the next request. Fix the underlying config first
        &mdash; otherwise it fails straight back to this state.
      </span>
      {error && <span className="w-full text-xs text-danger">{error}</span>}
    </div>
  )
}

function ProviderCard({ provider, onReset }: { provider: ProviderUsage; onReset: () => void }) {
  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-medium text-text">{provider.provider}</span>
          {!provider.enabled && <Badge tone="neutral">not in chain</Badge>}
        </div>
        <StatusBadge status={provider.status} />
      </div>

      <div className="mt-4">
        <LimitBar
          label="Daily request budget"
          used={provider.requests}
          limit={provider.budget}
          status={provider.status}
        />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Tokens (req/res)" value={`${provider.request_tokens} / ${provider.response_tokens}`} />
        <Stat label="Failures" value={provider.failures} />
        <Stat label="Fallbacks" value={provider.fallback_count} />
      </div>

      <div className="mt-3 border-t border-border pt-3 text-xs text-text-muted">
        Quota: <span className="text-text">{provider.quota_status}</span> · Health:{' '}
        <span className={provider.health.healthy ? 'text-success' : 'text-danger'}>
          {provider.health.state}
        </span>
        {provider.health.last_error && (
          <span className="ml-2 text-danger">— {provider.health.last_error}</span>
        )}
      </div>

      {!provider.health.healthy && (
        <ResetHealthButton provider={provider.provider} onDone={onReset} />
      )}
    </Panel>
  )
}

interface ProviderStatusPageProps {
  // Hoisted to App.tsx so its 15s poll is shared with the nav strip's compact bars
  // instead of each opening its own independent `/api/llm/usage` interval.
  llmUsage: ReturnType<typeof useLlmUsage>
}

export function ProviderStatusPage({ llmUsage }: ProviderStatusPageProps) {
  const { data, loading, error, refresh } = llmUsage

  return (
    <main className="px-6 py-10">
      <div className="mx-auto max-w-4xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-text-muted">
            Today's usage per LLM provider, plus each provider's live quota/health status.
          </p>
          <Button variant="ghost" onClick={refresh}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {/* Skeleton loaders (§5) instead of a blank page on first fetch -- shaped like
            a provider card (header row + quota bar + stat grid). `loading` here only
            covers the initial load (useLlmUsage.ts keeps it false across its 15s
            background polls), so this never flashes over an already-rendered page. */}
        {loading && (
          <div className="mt-6 space-y-3">
            {[0, 1, 2].map((i) => (
              <Panel key={i} className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-5 w-20 rounded" />
                </div>
                <Skeleton className="mt-4 h-2 w-full rounded-full" />
                <div className="mt-4 grid grid-cols-3 gap-4">
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                </div>
              </Panel>
            ))}
          </div>
        )}
        {error && <p className="mt-6 text-danger">Failed to load provider status: {error}</p>}

        {data && (
          <>
            <p className="mt-6 font-mono text-xs text-text-muted">
              Generated at {new Date(data.generated_at).toLocaleString()}
            </p>
            {data.providers.length === 0 && (
              <p className="mt-3 text-text-muted">No providers configured.</p>
            )}
            {/* Staggered fade/slide-in on load (§5). */}
            <StaggerList className="mt-3 space-y-3">
              {data.providers.map((provider) => (
                <StaggerItem key={provider.provider}>
                  <ProviderCard provider={provider} onReset={refresh} />
                </StaggerItem>
              ))}
            </StaggerList>
          </>
        )}
      </div>
    </main>
  )
}
