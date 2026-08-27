import type { SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from './utils'

/** Themed `<select>` matching `Input`'s styling, replacing the ad hoc `fieldClass()`
 * helper in Tasks.tsx (md-files/ui-development.md §4). Native select under the hood
 * (for platform accessibility/keyboard behavior) with a themed chevron overlay since
 * native option-list chrome can't be restyled.
 *
 * Usage:
 *   <Select value={status} onChange={(e) => setStatus(e.target.value)}>
 *     <option value="open">Open</option>
 *   </Select>
 */
export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        className={cn(
          'w-full appearance-none rounded-md border border-border bg-surface-2 px-3 py-1.5 pr-8 text-sm text-text transition-all duration-200 focus:border-primary focus:shadow-glow-primary focus:outline-none',
          className,
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
    </div>
  )
}
