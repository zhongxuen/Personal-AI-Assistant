import { useHealth } from '../hooks/useHealth'

export function StatusPage() {
  const health = useHealth()

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
      <div className="rounded-lg border border-slate-800 bg-slate-900 p-8 shadow-xl">
        <h1 className="text-2xl font-semibold">JARVIS</h1>
        <p className="mt-1 text-sm text-slate-400">Backend connectivity check</p>

        <div className="mt-6">
          {health.status === 'loading' && (
            <span className="text-slate-400">Checking backend…</span>
          )}
          {health.status === 'error' && (
            <span className="text-red-400">Backend unreachable: {health.message}</span>
          )}
          {health.status === 'ready' && (
            <span className="text-emerald-400">
              {health.data.service} — {health.data.status}
            </span>
          )}
        </div>
      </div>
    </main>
  )
}
