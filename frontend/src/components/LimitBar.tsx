import type { ProviderStatusBadge } from '../types/llmUsage'

// Same semantic colors as ProviderStatus.tsx's badge styles (§39), just as a fill
// color instead of a border/bg/text triple -- keeps the bar and the badge reading as
// the same signal at a glance.
const FILL_STYLES: Record<ProviderStatusBadge, string> = {
  NORMAL: 'bg-emerald-500',
  WARNING: 'bg-amber-500',
  CRITICAL: 'bg-red-500',
  FAILOVER: 'bg-purple-500',
}

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
 */
export function LimitBar({ label, used, limit, status, compact = false }: LimitBarProps) {
  const unmetered = limit === null
  // Cap the filled width at 100% even if `used` has crept past `limit` (AIRouter
  // fails over at FAILOVER, but a request already in flight when the threshold was
  // crossed can still land one row over budget) -- the bar communicates "full", not
  // a bar that visually overflows its own track.
  const percent = unmetered ? 100 : Math.min(100, Math.round((used / Math.max(limit, 1)) * 100))
  const fill = unmetered ? 'bg-slate-600' : FILL_STYLES[status] ?? 'bg-slate-500'

  return (
    <div className={compact ? 'min-w-[9rem]' : ''}>
      <div className={`flex items-baseline justify-between gap-2 ${compact ? 'text-[11px]' : 'text-xs'} text-slate-400`}>
        <span className="font-medium text-slate-300">{label}</span>
        <span>{unmetered ? 'unmetered (local)' : `${used} / ${limit} (${percent}%)`}</span>
      </div>
      <div
        className={`mt-1 w-full overflow-hidden rounded-full bg-slate-800 ${compact ? 'h-1.5' : 'h-2'}`}
        role="progressbar"
        aria-label={`${label} usage`}
        aria-valuenow={unmetered ? undefined : used}
        aria-valuemin={0}
        aria-valuemax={unmetered ? undefined : limit ?? undefined}
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${fill}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
