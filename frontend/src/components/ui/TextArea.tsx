import type { TextareaHTMLAttributes } from 'react'
import { cn } from './utils'

/** Themed `<textarea>` matching `Input`'s styling (md-files/ui-development.md §4).
 *
 * Usage: <TextArea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
 */
export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        'rounded-md border border-border bg-surface-2 px-3 py-1.5 text-sm text-text placeholder:text-text-muted transition-all duration-200 focus:border-primary focus:shadow-glow-primary focus:outline-none',
        className,
      )}
      {...rest}
    />
  )
}
