import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from './utils'

interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  children?: ReactNode
}

/** Glass-surface panel -- the themed replacement for the repeated
 * `rounded-lg border border-slate-800 bg-slate-900 p-4` pattern used ~15+ times across
 * pages today (md-files/ui-development.md §1/§4). Translucent background + blur +
 * hairline border instead of a flat fill.
 *
 * Usage: <Panel className="p-4">...</Panel>
 */
export function Panel({ className, children, ...rest }: PanelProps) {
  return (
    <div className={cn('rounded-lg border border-border bg-surface/70 backdrop-blur-md', className)} {...rest}>
      {children}
    </div>
  )
}

/** Alias so call sites can spell it `Card` where that reads better -- same component,
 * no behavior difference. */
export const Card = Panel
