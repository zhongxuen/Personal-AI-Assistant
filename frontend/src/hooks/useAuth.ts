import { useCallback, useEffect, useState } from 'react'
import { login as apiLogin, logout as apiLogout } from '../services/api'
import { isAuthenticated as readIsAuthenticated, onAuthChange } from '../services/auth'

interface UseAuthResult {
  isAuthenticated: boolean
  username: string | null
  loggingIn: boolean
  error: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

/** Mirrors `services/auth.ts`'s `localStorage` token into React state, re-rendering
 * every subscriber whenever the token changes -- on a successful login, an explicit
 * logout, or (see `services/api.ts`'s `ensureOk`) a 401 from any protected request
 * clearing an expired/invalid token out from under the app. `App.tsx` gates the whole
 * authenticated UI (dashboards, chat, voice) behind `isAuthenticated` from this hook so
 * all three of those paths land back on the login page the same way.
 */
export function useAuth(): UseAuthResult {
  const [isAuthenticated, setIsAuthenticated] = useState(readIsAuthenticated)
  const [username, setUsername] = useState<string | null>(null)
  const [loggingIn, setLoggingIn] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    return onAuthChange(() => {
      setIsAuthenticated(readIsAuthenticated())
    })
  }, [])

  // A page reload keeps the token (localStorage) but loses the in-memory username --
  // there's no "who am I" endpoint to re-fetch it from, so it's simply unknown again
  // until the next login. Nothing in the UI depends on it beyond a nice-to-have label.
  useEffect(() => {
    if (!isAuthenticated) setUsername(null)
  }, [isAuthenticated])

  const login = useCallback(async (usernameInput: string, password: string) => {
    setLoggingIn(true)
    setError(null)
    try {
      const result = await apiLogin(usernameInput, password)
      setUsername(result.username)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.')
      throw err
    } finally {
      setLoggingIn(false)
    }
  }, [])

  const logout = useCallback(() => {
    apiLogout()
  }, [])

  return { isAuthenticated, username, loggingIn, error, login, logout }
}
