/** Bearer-token storage for the web login flow (§34, file 12 prompt 2).
 *
 * A plain `localStorage` string, not React state -- `services/api.ts` (which has no
 * React dependency of its own) reads it directly on every protected request via
 * `authHeaders()`, and `hooks/useAuth.ts` mirrors it into component state by listening
 * for the `jarvis-auth-change` event this module dispatches on every write/clear, so
 * both stay in sync without threading the token through every call site by hand.
 */

const TOKEN_KEY = 'jarvis_token'
const AUTH_CHANGE_EVENT = 'jarvis-auth-change'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
  window.dispatchEvent(new Event(AUTH_CHANGE_EVENT))
}

/** Drops the stored token -- called on explicit logout, and by `services/api.ts`
 * whenever a protected request comes back 401 (an expired/invalid/revoked token),
 * so a stale token left in `localStorage` doesn't keep bouncing every request off
 * the backend forever without ever prompting the user to log back in.
 */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
  window.dispatchEvent(new Event(AUTH_CHANGE_EVENT))
}

export function isAuthenticated(): boolean {
  return getToken() !== null
}

/** `Authorization` header (or `{}` when logged out) to spread into a protected
 * request's headers -- see docs/security.md's "Authentication" section for which
 * routes actually require this.
 */
export function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** Subscribes to login/logout/token-expiry changes; returns an unsubscribe function
 * (same shape `useEffect` expects back from its setup callback).
 */
export function onAuthChange(listener: () => void): () => void {
  window.addEventListener(AUTH_CHANGE_EVENT, listener)
  return () => window.removeEventListener(AUTH_CHANGE_EVENT, listener)
}
