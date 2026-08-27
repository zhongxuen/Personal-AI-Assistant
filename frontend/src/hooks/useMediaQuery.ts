import { useEffect, useState } from 'react'

/** Tracks whether a CSS media query currently matches, updating live as the viewport
 * crosses the breakpoint. Used by App.tsx to know when the sidebar has left its
 * `md:static` desktop layout and become an off-canvas mobile drawer (md-files/
 * ui-development.md §3/§7 milestone 6), so it can `inert` the drawer while it's
 * off-screen instead of leaving its nav buttons in the tab order.
 *
 * Usage: const isDesktop = useMediaQuery('(min-width: 768px)')
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)

  useEffect(() => {
    const mql = window.matchMedia(query)
    const onChange = () => setMatches(mql.matches)
    onChange()
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [query])

  return matches
}
