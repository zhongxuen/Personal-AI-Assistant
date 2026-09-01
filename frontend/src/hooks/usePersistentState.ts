import { useCallback, useState } from 'react'

/** `useState` backed by `localStorage` under `key`, so in-progress UI state survives
 * both a manual browser refresh and Chrome discarding a background tab to reclaim
 * memory (which reloads it from scratch the next time it's foregrounded -- from the
 * user's perspective indistinguishable from "I switched tabs and lost my progress").
 * Mirrors `useState`'s lazy-initializer form (`initialValue` may be a value or a
 * `() => value` thunk) so callers like `crypto.randomUUID()` aren't re-run every
 * render just to be discarded.
 *
 * Falls back to `initialValue` when nothing's stored yet, or when the stored JSON
 * doesn't parse (e.g. `localStorage` unavailable in private browsing, or a shape left
 * over from a previous build) -- a stale/corrupt cache should never hard-fail the app.
 */
export function usePersistentState<T>(key: string, initialValue: T | (() => T)) {
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = window.localStorage.getItem(key)
      if (stored !== null) return JSON.parse(stored) as T
    } catch {
      // Corrupt/foreign JSON under this key -- fall through to initialValue below.
    }
    return initialValue instanceof Function ? initialValue() : initialValue
  })

  // Memoized on `key` so the returned setter is referentially stable across renders,
  // exactly like the plain `useState` setter it wraps. Without this it was a fresh
  // function every render, which quietly makes it unusable as a `useEffect`/`useMemo`
  // dependency: listing it re-runs the effect on every render, and omitting it trips
  // exhaustive-deps and leaves the effect closing over a stale copy.
  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const resolved = next instanceof Function ? next(prev) : next
        try {
          window.localStorage.setItem(key, JSON.stringify(resolved))
        } catch {
          // Quota exceeded or storage disabled -- state still updates in memory for this
          // tab's lifetime, it just won't survive a refresh.
        }
        return resolved
      })
    },
    [key],
  )

  return [value, set] as const
}
