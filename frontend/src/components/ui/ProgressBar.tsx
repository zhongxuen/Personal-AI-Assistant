import type { HTMLAttributes } from 'react'
import type { BadgeTone } from './Badge'
import { cn } from './utils'

const FILL_STYLES: Record<BadgeTone, string> = {
  primary: 'bg-primary',
  secondary: 'bg-secondary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  neutral: 'bg-text-muted',
}

// `animate-glow-pulse` reads `currentColor` for its box-shadow (index.css), so the
// pulse tint is set here via `text-*` rather than baked into the keyframe itself.
const GLOW_STYLES: Record<BadgeTone, string> = {
  primary: 'text-primary',
  secondary: 'text-secondary',
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  neutral: 'text-text-muted',
}

interface ProgressBarProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  /** 0-100. Values outside that range are clamped so the fill never overflows its track. */
  value: number
  tone?: BadgeTone
  label?: string
  /** Denser height for tight layouts (nav strips, compact cards). */
  compact?: boolean
  /** Glow-pulses the fill (§5) -- e.g. a quota bar that just crossed into
   * WARNING/CRITICAL. Renders as an animated neon box-shadow, not an opacity fade, so
   * it reads as "alert" rather than "still loading". */
  pulse?: boolean
}

/** Themed progress-bar primitive generalizing the ad hoc bar in LimitBar.tsx
 * (md-files/ui-development.md §4) -- LimitBar keeps owning the used/limit/status
 * framing and becomes a thin wrapper around this once pages migrate.
 *
 * Usage: <ProgressBar value={72} tone="warning" label="Gemini" />
 */
export function ProgressBar({
  value,
  tone = 'primary',
  label,
  compact = false,
  pulse = false,
  className,
  ...rest
}: ProgressBarProps) {
  const percent = Math.min(100, Math.max(0, value))

  return (
    <div className={cn(compact ? 'min-w-[9rem]' : '', className)} {...rest}>
      {label && <div className={cn('text-text-muted', compact ? 'text-[11px]' : 'text-xs')}>{label}</div>}
      <div
        className={cn('mt-1 w-full overflow-hidden rounded-full bg-surface-2', compact ? 'h-1.5' : 'h-2')}
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            FILL_STYLES[tone],
            pulse && 'animate-glow-pulse',
            pulse && GLOW_STYLES[tone],
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
