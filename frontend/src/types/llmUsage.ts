// Mirrors backend/app/api/routes/llm_usage.py's response models.

export interface ProviderHealth {
  state: string
  healthy: boolean
  last_error: string | null
}

/** The four badge values §39's MVP UI calls for -- see `_status_badge` in
 * llm_usage.py for how `status` is derived from `quota_status` + health.
 */
export type ProviderStatusBadge = 'NORMAL' | 'WARNING' | 'CRITICAL' | 'FAILOVER'

/** What a provider's budget is metered in: OpenAI's daily spend in USD, Gemini's
 * daily request count, or null when unmetered (Ollama -- local and free). */
export type LimitUnit = 'usd' | 'requests'

export interface ProviderUsage {
  provider: string
  enabled: boolean
  requests: number
  request_tokens: number
  response_tokens: number
  /** Input tokens served from the provider's prompt cache today (0 if not reported). */
  cached_tokens: number
  /** Estimated spend today in USD (0 for providers that don't report cost). */
  cost_usd: number
  failures: number
  fallback_count: number
  unit: LimitUnit | null
  /** Today's usage in `unit` -- exactly what the backend's QuotaManager enforces. */
  used: number
  /** Today's limit in `unit`, or null when unmetered. */
  limit: number | null
  /** Optional monthly budget (OpenAI only); both null when not configured. */
  monthly_used: number | null
  monthly_limit: number | null
  /** Same value as `limit`; kept for older builds of this client. */
  budget: number | null
  quota_status: string
  status: ProviderStatusBadge
  health: ProviderHealth
}

export interface LLMUsageResponse {
  generated_at: string
  providers: ProviderUsage[]
}
