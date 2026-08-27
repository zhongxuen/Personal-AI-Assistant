import type { HTMLAttributes } from 'react'
import { cn } from './utils'

/** Shimmering placeholder block for in-flight data fetches, replacing blank/empty
 * states while pages load (md-files/ui-development.md §2/§5). Size it with className
 * (e.g. `h-4 w-32`); defaults to a rounded bar sized for a line of text. The shimmer
 * sweep (`animate-shimmer`) is defined alongside the color/motion tokens in index.css.
 *
 * Usage: {loading ? <Skeleton className="h-4 w-40" /> : <span>{value}</span>}
 */
export function Skeleton({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'h-4 w-full animate-shimmer rounded-md bg-gradient-to-r from-surface-2 via-border to-surface-2 bg-[length:200%_100%]',
        className,
      )}
      {...rest}
    />
  )
}
