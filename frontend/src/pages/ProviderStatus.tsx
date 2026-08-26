import { useLlmUsage } from '../hooks/useLlmUsage'
import type { ProviderStatusBadge, ProviderUsage } from '../types/llmUsage'

// §39 MVP UI requirement: one badge per provider, NORMAL/WARNING/CRITICAL/FAILOVER.
const BADGE_STYLES: Record<ProviderStatusBadge, string> = {
  NORMAL: 'border-emerald-800 bg-emerald-950/40 text-emerald-300',
  WARNING: 'border-amber-800 bg-amber-950/40 text-amber-300',
  CRITICAL: 'border-red-800 bg-red-950/40 text-red-300',
  FAILOVER: 'border-purple-800 bg-purple-950/40 text-purple-300',
}

function StatusBadge({ status }: { status: ProviderStatusBadge }) {
  const style = BADGE_STYLES[status] ?? 'border-slate-700 bg-slate-800/60 text-slate-300'
  return (
    <span className={`rounded border px-2 py-0.5 text-xs font-semibold tracking-wide ${style}`}>
      {status}
    </span>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-100">{value}</div>
    </div>
  )
}

function ProviderCard({ provider }: { provider: ProviderUsage }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-100">{provider.provider}</span>
          {!provider.enabled && (
            <span className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-500">
              not in chain
            </span>
          )}
        </div>
        <StatusBadge status={provider.status} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Requests today" value={provider.requests} />
        <Stat label="Tokens (req/res)" value={`${provider.request_tokens} / ${provider.response_tokens}`} />
        <Stat label="Failures" value={provider.failures} />
        <Stat label="Fallbacks" value={provider.fallback_count} />
      </div>

      <div className="mt-3 border-t border-slate-800 pt-3 text-xs text-slate-500">
        Quota: <span className="text-slate-300">{provider.quota_status}</span> · Health:{' '}
        <span className={provider.health.healthy ? 'text-emerald-400' : 'text-red-400'}>
          {provider.health.state}
        </span>
        {provider.health.last_error && (
          <span className="ml-2 text-red-400">— {provider.health.last_error}</span>
        )}
      </div>
    </div>
  )
}

export function ProviderStatusPage() {
  const { data, loading, error, refresh } = useLlmUsage()

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto max-w-4xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">AI Provider Status</h1>
            <p className="mt-1 text-sm text-slate-400">
              Today's usage per LLM provider, plus each provider's live quota/health status.
            </p>
          </div>
          <button
            onClick={refresh}
            className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            Refresh
          </button>
        </div>

        {loading && <p className="mt-6 text-slate-400">Loading provider status…</p>}
        {error && <p className="mt-6 text-red-400">Failed to load provider status: {error}</p>}

        {data && (
          <>
            <p className="mt-6 text-xs text-slate-500">
              Generated at {new Date(data.generated_at).toLocaleString()}
            </p>
            <div className="mt-3 space-y-3">
              {data.providers.length === 0 && (
                <p className="text-slate-400">No providers configured.</p>
              )}
              {data.providers.map((provider) => (
                <ProviderCard key={provider.provider} provider={provider} />
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  )
}
