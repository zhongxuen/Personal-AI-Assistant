// Mirrors backend/app/api/routes/activity.py's response models.

export type ActivityType = 'tool_call' | 'llm_call'
export type ActivityStatus = 'ok' | 'error'

export interface ActivityItem {
  id: string
  type: ActivityType
  timestamp: string
  status: ActivityStatus
  summary: string
  // Populated for type === 'tool_call'.
  tool_name: string | null
  scope_label: string | null
  platform: string | null
  error: string | null
  // Populated for type === 'llm_call'.
  provider: string | null
  model: string | null
  request_tokens: number | null
  response_tokens: number | null
  fallback_used: boolean | null
  latency: number | null
}

export interface ActivityResponse {
  generated_at: string
  items: ActivityItem[]
}
