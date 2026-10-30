import type { LimitUnit, ProviderStatusBadge } from '../types/llmUsage'
import { formatLimit } from './limitFormat'
import type { BadgeTone } from './ui/Badge'
import { PROVIDER_STATUS_TONE } from './ui/Badge'
import { ProgressBar } from './ui/ProgressBar'
import { cn } from './ui/utils'

interface LimitBarProps {
  label: string
  used: number
  /** Today's limit in `unit`, or null when this provider is unmetered (e.g. Ollama
   * running locally -- there's no cloud quota or spend to bar-chart). */
  limit: number | null
  /** `'usd'` renders money ("$0.42 / $1.00"), `'requests'` a count ("8 / 80"). */
  unit: LimitUnit | null
  status: ProviderStatusBadge
  /** False for a provider that's configured but out of the chain (e.g. Gemini, which
   * is opt-in) -- rendered as "disabled" rather than as a live budget. */
  enabled?: boolean
  /** Denser layout for the always-visible nav strip vs. the full provider card. */
  compact?: boolean
}

/** A used/limit progress bar for one provider's daily budget (§8/§39) -- spend in
 * USD for OpenAI, a request count for Gemini. Polls nothing itself -- it just renders
 * whatever `useLlmUsage` last fetched, so it updates automatically on that hook's
 * existing interval every time a caller re-renders with fresh props.
 *
 * Thin wrapper around the shared `ui/ProgressBar` primitive (md-files/ui-development.md
 * §4) -- owns the used/limit/status framing and the label row, delegates the actual
 * track/fill rendering. The fill tone reuses `PROVIDER_STATUS_TONE` so this bar and
 * `ProviderStatus.tsx`'s badges read as the same signal, and glow-pulses once a
 * metered provider is at WARNING/CRITICAL (§5), building on the bar's existing width
 * transition.
 */
export function LimitBar({
  label,
  used,
  limit,
  unit,
  status,
  enabled = true,
  compact = false,
}: LimitBarProps) {
  const metered = enabled && limit !== null && unit !== null
  // Cap the filled width at 100% even if `used` has crept past `limit` (AIRouter
  // fails over at FAILOVER, but a request already in flight when the threshold was
  // crossed can still land one row over budget) -- the bar communicates "full", not
  // a bar that visually overflows its own track.
  const percent = metered ? Math.min(100, Math.round((used / Math.max(limit, Number.EPSILON)) * 100)) : 0
  const tone: BadgeTone = metered ? PROVIDER_STATUS_TONE[status] : 'neutral'
  const pulse = metered && (status === 'WARNING' || status === 'CRITICAL')

  return (
    <div className={cn(compact ? 'min-w-[9rem]' : '', !enabled && 'opacity-60')}>
      <div
        className={cn(
          'flex items-baseline justify-between gap-2 text-text-muted',
          compact ? 'text-[11px]' : 'text-xs',
        )}
      >
        <span className="font-medium text-text">{label}</span>
        <span className="font-mono">{formatLimit(used, limit, unit, enabled)}</span>
      </div>
      <ProgressBar
        // Unmetered: a full neutral track ("no limit"); disabled: an empty one.
        value={metered ? percent : enabled ? 100 : 0}
        tone={tone}
        compact={compact}
        pulse={pulse}
        aria-label={`${label} usage`}
      />
    </div>
  )
}
