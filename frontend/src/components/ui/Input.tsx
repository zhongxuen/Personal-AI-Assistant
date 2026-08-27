import type { InputHTMLAttributes } from 'react'
import { cn } from './utils'

/** Text input themed with the Cyberpunk HUD tokens, replacing the ad hoc
 * `fieldClass()` helper in Tasks.tsx (md-files/ui-development.md §4). Glows on focus
 * via box-shadow rather than filter, per the motion-performance guidance in §2.
 *
 * Usage: <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. work" />
 */
export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'rounded-md border border-border bg-surface-2 px-3 py-1.5 text-sm text-text placeholder:text-text-muted transition-all duration-200 focus:border-primary focus:shadow-glow-primary focus:outline-none',
        className,
      )}
      {...rest}
    />
  )
}
