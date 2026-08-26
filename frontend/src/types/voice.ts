/** Mirrors backend/app/api/routes/voice.py's VoiceMessageResponse. `text`/`tool_calls`/
 * `used_llm`/`provider`/`audio_base64` are only populated on a non-dry-run call --
 * a dry-run (STT-preview) response only ever carries `transcript`.
 */
export interface VoiceToolCallResult {
  tool_name: string
  params: Record<string, unknown>
  result: {
    success: boolean
    data: Record<string, unknown> | null
    error: string | null
  }
}

export interface VoiceMessageResponse {
  transcript: string
  text: string | null
  tool_calls: VoiceToolCallResult[]
  used_llm: boolean
  provider: string | null
  audio_base64: string | null
}
