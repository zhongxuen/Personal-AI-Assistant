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

/** Mirrors backend/app/core/models.py's AssistantStreamEvent -- one event from the SSE
 * endpoint POST /api/assistant/stream.
 *
 * `done` always arrives exactly once, last, and its `response` is authoritative: it is
 * byte-for-byte what the non-streaming POST /api/assistant/message would have returned
 * for the same message. Everything before it is a preview, so a consumer that renders
 * deltas as they arrive and then replaces them with `response.text` can never end up
 * displaying something the backend didn't actually conclude.
 *
 * There is deliberately no `error` variant -- a failed turn is still a `done` whose
 * `response.text` explains what went wrong, so there is exactly one terminal case.
 */
export interface AssistantStreamEvent {
  type: 'delta' | 'tool' | 'done'
  text: string
  tool_call: AssistantToolCallResult | null
  response: AssistantResponse | null
}
