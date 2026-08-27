import type { HTMLAttributes, ReactNode } from 'react'
import type { ProviderStatusBadge } from '../../types/llmUsage'
import { cn } from './utils'

export type BadgeTone = 'primary' | 'secondary' | 'success' | 'warning' | 'danger' | 'neutral'

const TONE_STYLES: Record<BadgeTone, string> = {
  primary: 'border-primary/60 bg-primary/10 text-primary',
  secondary: 'border-secondary/60 bg-secondary/10 text-secondary',
  success: 'border-success/60 bg-success/10 text-success',
  warning: 'border-warning/60 bg-warning/10 text-warning',
  danger: 'border-danger/60 bg-danger/10 text-danger',
  neutral: 'border-border bg-surface-2/60 text-text-muted',
}

/** Maps the four provider-status values (ProviderStatus.tsx `BADGE_STYLES` /
 * LimitBar.tsx `FILL_STYLES` today) onto badge tones, per the old-class -> new-token
 * mapping table in md-files/ui-development.md §2 -- FAILOVER rides the secondary
 * (violet) accent since it's "on the backup provider", not itself a success/warn/danger
 * signal.
 */
export const PROVIDER_STATUS_TONE: Record<ProviderStatusBadge, BadgeTone> = {
  NORMAL: 'success',
  WARNING: 'warning',
  CRITICAL: 'danger',
  FAILOVER: 'secondary',
}

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone
  children?: ReactNode
}

/** Status pill consuming the token-driven tone palette (md-files/ui-development.md
 * §2/§4). Pick a semantic `tone` directly, or look one up via
 * `PROVIDER_STATUS_TONE[status]` for the NORMAL/WARNING/CRITICAL/FAILOVER provider
 * states so the badge and the quota bar keep reading as the same signal.
 *
 * Usage: <Badge tone={PROVIDER_STATUS_TONE[status]}>{status}</Badge>
 */
export function Badge({ tone = 'neutral', className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold tracking-wide',
        TONE_STYLES[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  )
}
