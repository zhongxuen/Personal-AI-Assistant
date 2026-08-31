import { useState } from 'react'
import type { FormEvent } from 'react'
import { motion } from 'framer-motion'
import { Cpu, KeyRound, User as UserIcon } from 'lucide-react'
import { Button, Input } from '../components/ui'

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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg px-6 text-text">
      {/* Ambient animated gradient backdrop (§5) standing in for a particle field --
          slow-pulsing neon blobs via plain Tailwind `animate-pulse`, which
          automatically respects the global `prefers-reduced-motion` override in
          index.css rather than needing bespoke handling here. */}
      <div className="pointer-events-none absolute inset-0" aria-hidden="true">
        <div className="absolute -left-24 -top-24 h-96 w-96 animate-pulse rounded-full bg-primary/20 blur-3xl [animation-duration:6s]" />
        <div className="absolute -bottom-32 -right-16 h-[28rem] w-[28rem] animate-pulse rounded-full bg-secondary/20 blur-3xl [animation-duration:8s]" />
        <div className="absolute left-1/3 top-1/2 h-64 w-64 -translate-y-1/2 animate-pulse rounded-full bg-primary/10 blur-3xl [animation-duration:10s]" />
      </div>

      <motion.form
        onSubmit={handleSubmit}
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: 'easeOut' }}
        className="relative w-full max-w-sm rounded-lg border border-border bg-surface/70 p-6 backdrop-blur-md"
      >
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-primary/50 bg-primary/10 text-primary shadow-glow-primary">
            <Cpu className="h-4 w-4" />
          </div>
          <div>
            <h1 className="font-display text-xl font-semibold tracking-wide text-text">Jarvis</h1>
            <p className="text-sm text-text-muted">Sign in to continue.</p>
          </div>
        </div>

        <label
          htmlFor="login-username"
          className="mt-6 block text-xs font-medium uppercase tracking-wide text-text-muted"
        >
          Username
        </label>
        {/* Icon-adorned inputs reuse the shared `Input` primitive's own glowing
            focus-state (border-brighten + box-shadow), so the "glowing input focus
            states" touch from §5 comes free of the primitive rather than being
            reimplemented per-page. */}
        <div className="relative mt-1">
          <UserIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            id="login-username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full pl-9"
          />
        </div>

        <label
          htmlFor="login-password"
          className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted"
        >
          Password
        </label>
        <div className="relative mt-1">
          <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <Input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full pl-9"
          />
        </div>

        {error && <p className="mt-3 text-sm text-danger">{error}</p>}

        <Button
          type="submit"
          disabled={loggingIn || !username || !password}
          loading={loggingIn}
          className="mt-6 w-full"
        >
          Sign in
        </Button>
      </motion.form>
    </main>
  )
}
