import { useState } from 'react'
import type { FormEvent } from 'react'

interface LoginPageProps {
  onLogin: (username: string, password: string) => Promise<void>
  loggingIn: boolean
  error: string | null
}

/** Web login (§34, file 12 prompt 2) -- the one screen shown before `App.tsx` gates
 * open the dashboards/chat/voice. Posts to POST /api/auth/login via `useAuth`; a
 * desktop-local deployment never needs this (platform="desktop" requests skip the
 * auth layer entirely, see docs/security.md), but the same frontend build is served
 * both ways, so this always renders first until a token exists either way.
 */
export function LoginPage({ onLogin, loggingIn, error }: LoginPageProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!username || !password) return
    try {
      await onLogin(username, password)
    } catch {
      // Error state is already surfaced via the `error` prop -- nothing further to do.
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-6 text-slate-100">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-6"
      >
        <h1 className="text-xl font-semibold">Jarvis</h1>
        <p className="mt-1 text-sm text-slate-400">Sign in to continue.</p>

        <label className="mt-6 block text-xs font-medium uppercase tracking-wide text-slate-500">
          Username
        </label>
        <input
          autoFocus
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-slate-500 focus:outline-none"
        />

        <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-slate-500">
          Password
        </label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:border-slate-500 focus:outline-none"
        />

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={loggingIn || !username || !password}
          className="mt-6 w-full rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {loggingIn ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
