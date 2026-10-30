// Budget formatting shared by LimitBar and the Providers page -- kept out of
// LimitBar.tsx so that file only exports components (React fast refresh).
import type { LimitUnit } from '../types/llmUsage'

/** `$0.42` -- always two decimals, so a spend bar never reads "$0.4 / $1". */
export function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`
}

/** The right-hand text of a limit bar, for any provider state. Exported so the same
 * wording is used wherever a budget is summarized. */
export function formatLimit(
  used: number,
  limit: number | null,
  unit: LimitUnit | null,
  enabled = true,
): string {
  if (!enabled) return 'disabled'
  if (limit === null || unit === null) return 'unmetered (local)'
  const percent = Math.round((used / Math.max(limit, Number.EPSILON)) * 100)
  if (unit === 'usd') return `${formatUsd(used)} / ${formatUsd(limit)} (${percent}%)`
  return `${used} / ${limit} (${percent}%)`
}

/** Default label for a provider's daily bar, by unit. */
export function limitLabel(unit: LimitUnit | null): string {
  if (unit === 'usd') return 'Daily spend'
  if (unit === 'requests') return 'Daily requests'
  return 'Usage'
}
