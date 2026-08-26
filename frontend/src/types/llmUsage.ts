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

export interface ProviderUsage {
  provider: string
  enabled: boolean
  requests: number
  request_tokens: number
  response_tokens: number
  failures: number
  fallback_count: number
  quota_status: string
  status: ProviderStatusBadge
  health: ProviderHealth
}

export interface LLMUsageResponse {
  generated_at: string
  providers: ProviderUsage[]
}
