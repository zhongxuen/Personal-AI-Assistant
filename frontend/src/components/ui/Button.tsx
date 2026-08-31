import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from './utils'

export type ButtonVariant = 'primary' | 'secondary' | 'destructive' | 'ghost'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  loading?: boolean
  children?: ReactNode
}

// Neon glow on hover via box-shadow (not filter, per the perf guidance in
// md-files/ui-development.md §2) using the same color-mix formula as the
// --shadow-glow-primary/secondary tokens in index.css, extended to danger since that
// token wasn't defined there.
const VARIANT_STYLES: Record<ButtonVariant, string> = {
  primary: 'border border-primary/60 bg-primary text-bg hover:shadow-glow-primary',
  secondary: 'border border-secondary/60 bg-secondary text-bg hover:shadow-glow-secondary',
  destructive:
    'border border-danger/60 bg-danger text-white hover:shadow-[0_0_12px_1px_color-mix(in_srgb,var(--color-danger)_60%,transparent)]',
  ghost: 'border border-transparent bg-transparent text-text hover:border-border hover:bg-surface-2',
}

/** Primary/secondary/destructive/ghost button themed with the Cyberpunk HUD tokens
 * (md-files/ui-development.md §2/§4). Pass `loading` to swap the label for a spinner
 * and disable interaction while an async action is in flight.
 *
 * Usage: <Button variant="primary" loading={isSaving} onClick={save}>Save</Button>
 */
export function Button({
  variant = 'primary',
  loading = false,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50',
        VARIANT_STYLES[variant],
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  )
}
