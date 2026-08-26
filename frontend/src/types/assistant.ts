/** Mirrors backend/app/core/models.py's AssistantResponse -- the same shape every
 * platform adapter gets back from POST /api/assistant/message (desktop, web, voice's
 * own VoiceMessageResponse wraps the same fields plus a transcript). See
 * frontend/src/pages/Chat.tsx (file 12 prompt 2), which is the web platform's adapter.
 */
export interface AssistantToolCallResult {
  tool_name: string
  params: Record<string, unknown>
  result: {
    success: boolean
    data: Record<string, unknown> | null
    error: string | null
  }
}

export interface AssistantResponse {
  text: string
  tool_calls: AssistantToolCallResult[]
  used_llm: boolean
  provider: string | null
}
