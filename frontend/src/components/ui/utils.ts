/** Joins conditional class-name fragments, skipping any falsy value. No conflict
 * resolution (there's no tailwind-merge dependency in this project) -- callers just
 * need to avoid passing two utilities for the same CSS property in one className.
 *
 * Usage: cn('rounded-md', active && 'text-primary', className)
 */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}
