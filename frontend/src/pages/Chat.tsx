import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { sendChatMessage } from '../services/api'
import type { AssistantToolCallResult } from '../types/assistant'

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  usedLlm?: boolean
  provider?: string | null
  toolCalls?: AssistantToolCallResult[]
}

/** A tool call whose result is a platform-capability rejection (§22) -- ToolExecutor's
 * `"This action isn't available on <platform>."` (backend/app/core/tool_executor.py).
 * Flagged separately from an ordinary tool failure only so it can render with a
 * distinct, expected-not-alarming style instead of looking like an error.
 */
function isCapabilityRejection(call: AssistantToolCallResult): boolean {
  return !call.result.success && (call.result.error?.includes("isn't available on") ?? false)
}

/** Web chat (§37 Phase 11, file 12 prompt 2) -- POSTs to the exact same
 * /api/assistant/message endpoint desktop/voice use, platform="web" (see
 * services/api.ts's sendChatMessage), so a message asking for a desktop-only tool
 * (e.g. "open vscode") comes back with the same §22 explanatory rejection any other
 * platform-capability check produces, not a silent no-op or an actual attempt to
 * control this machine -- there's nothing web-specific to gate here beyond just
 * sending the message and rendering whatever AssistantCore actually returns.
 */
export function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const conversationId = useRef<string>(crypto.randomUUID())

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    setMessages((prev) => [...prev, { role: 'user', text }])
    setInput('')
    setSending(true)
    setError(null)

    try {
      const response = await sendChatMessage(text, conversationId.current)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: response.text,
          usedLlm: response.used_llm,
          provider: response.provider,
          toolCalls: response.tool_calls,
        },
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message.')
    } finally {
      setSending(false)
    }
  }

  return (
    <main className="flex min-h-screen flex-col bg-slate-950 px-6 py-10 text-slate-100">
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col">
        <h1 className="text-2xl font-semibold">Chat</h1>
        <p className="mt-1 text-sm text-slate-400">
          Talk to Jarvis over the web -- same assistant, same tools, minus anything that needs
          this machine.
        </p>

        <div className="mt-6 flex-1 space-y-3 overflow-y-auto">
          {messages.length === 0 && (
            <p className="text-sm text-slate-500">Say something to get started.</p>
          )}
          {messages.map((message, index) => (
            <div
              key={index}
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                message.role === 'user'
                  ? 'ml-auto bg-slate-800 text-slate-100'
                  : 'bg-slate-900 text-slate-200'
              }`}
            >
              {message.role === 'assistant' && (
                <p className="mb-1 text-xs uppercase tracking-wide text-slate-500">
                  {message.usedLlm ? `Reasoned (${message.provider ?? 'LLM'})` : 'Direct command'}
                </p>
              )}
              <p className="whitespace-pre-wrap">{message.text}</p>
              {message.toolCalls?.map((call, callIndex) => (
                <p
                  key={callIndex}
                  className={`mt-1 font-mono text-xs ${
                    isCapabilityRejection(call)
                      ? 'text-amber-400'
                      : call.result.success
                        ? 'text-emerald-400'
                        : 'text-red-400'
                  }`}
                >
                  {call.tool_name}: {call.result.success ? 'ok' : call.result.error}
                </p>
              ))}
            </div>
          ))}
        </div>

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}

        <form onSubmit={handleSubmit} className="mt-4 flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message Jarvis…"
            className="flex-1 rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 focus:border-slate-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </form>
      </div>
    </main>
  )
}
