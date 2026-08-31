import type { ProviderStatusBadge } from '../types/llmUsage'
import type { BadgeTone } from './ui/Badge'
import { PROVIDER_STATUS_TONE } from './ui/Badge'
import { ProgressBar } from './ui/ProgressBar'
import { cn } from './ui/utils'

interface LimitBarProps {
  label: string
  used: number
  /** Today's internal request budget, or null when this provider is unmetered (e.g.
   * Ollama running locally -- there's no cloud quota to bar-chart). */
  limit: number | null
  status: ProviderStatusBadge
  /** Denser layout for the always-visible nav strip vs. the full provider card. */
  compact?: boolean
}

/** A used/limit progress bar for one provider's daily request budget (§8/§39). Polls
 * nothing itself -- it just renders whatever `useLlmUsage` last fetched, so it
 * updates automatically on that hook's existing interval every time a caller re-
 * renders with fresh `used`/`limit`/`status` props.
 *
 * Thin wrapper around the shared `ui/ProgressBar` primitive (md-files/ui-development.md
 * §4) -- owns the used/limit/status framing and the label row, delegates the actual
 * track/fill rendering. The fill tone reuses `PROVIDER_STATUS_TONE` so this bar and
 * `ProviderStatus.tsx`'s badges read as the same signal, and glow-pulses once a
 * metered provider is at WARNING/CRITICAL (§5), building on the bar's existing width
 * transition.
 */
export function LimitBar({ label, used, limit, status, compact = false }: LimitBarProps) {
  const unmetered = limit === null
  // Cap the filled width at 100% even if `used` has crept past `limit` (AIRouter
  // fails over at FAILOVER, but a request already in flight when the threshold was
  // crossed can still land one row over budget) -- the bar communicates "full", not
  // a bar that visually overflows its own track.
  const percent = unmetered ? 100 : Math.min(100, Math.round((used / Math.max(limit, 1)) * 100))
  const tone: BadgeTone = unmetered ? 'neutral' : PROVIDER_STATUS_TONE[status]
  const pulse = !unmetered && (status === 'WARNING' || status === 'CRITICAL')

  return (
    <div className={compact ? 'min-w-[9rem]' : ''}>
      <div
        className={cn(
          'flex items-baseline justify-between gap-2 text-text-muted',
          compact ? 'text-[11px]' : 'text-xs',
        )}
      >
        <span className="font-medium text-text">{label}</span>
        <span>{unmetered ? 'unmetered (local)' : `${used} / ${limit} (${percent}%)`}</span>
      </div>
      <ProgressBar
        value={percent}
        tone={tone}
        compact={compact}
        pulse={pulse}
        aria-label={`${label} usage`}
      />
    </div>
  )
}
