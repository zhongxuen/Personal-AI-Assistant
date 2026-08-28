import { useState } from 'react'
import type { FormEvent } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Bot, Send, User } from 'lucide-react'
import { sendChatMessage } from '../services/api'
import type { AssistantToolCallResult } from '../types/assistant'
import { Button, Input } from '../components/ui'
import { cn } from '../components/ui/utils'
import { usePersistentState } from '../hooks/usePersistentState'

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
  // Persisted (§ user report) so refreshing the page -- or Chrome discarding this tab
  // in the background -- doesn't wipe the conversation. `conversationId` is persisted
  // alongside the messages rather than regenerated per mount so a resumed session
  // keeps talking to the same backend conversation instead of silently starting a new
  // one the next message goes to.
  const [messages, setMessages] = usePersistentState<ChatMessage[]>('jarvis:chat:messages', [])
  const [conversationId] = usePersistentState<string>('jarvis:chat:conversationId', () =>
    crypto.randomUUID(),
  )
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    setMessages((prev) => [...prev, { role: 'user', text }])
    setInput('')
    setSending(true)
    setError(null)

    try {
      const response = await sendChatMessage(text, conversationId)
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

  // `pb-28` on mobile clears the fixed VoiceControl FAB (bottom-6 + button + "Hold to
  // talk" label, ~100px), which on a phone-width viewport sits directly on top of this
  // composer's Send button. Desktop has gutters wide enough that it never overlaps, so
  // the extra padding is dropped from `sm:` up.
  return (
    <main className="flex h-full flex-col px-4 pb-28 pt-4 sm:px-6 sm:py-6">
      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col overflow-hidden">
        <p className="text-sm text-text-muted">
          Talk to Jarvis over the web -- same assistant, same tools, minus anything that needs
          this machine.
        </p>

        <div className="mt-6 flex-1 space-y-3 overflow-y-auto pr-1">
          {messages.length === 0 && !sending && (
            <p className="text-sm text-text-muted">Say something to get started.</p>
          )}

          {/* Staggered fade/slide-in entrance per md-files/ui-development.md §5;
              `initial={false}` on the group keeps messages already on screen from
              re-animating on unrelated re-renders (e.g. the typing indicator toggling). */}
          <AnimatePresence initial={false}>
            {messages.map((message, index) => (
              <motion.div
                key={index}
                initial={{ opacity: 0, y: 10, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.25, ease: 'easeOut' }}
                className={cn(
                  'flex max-w-[92%] items-start gap-2 sm:max-w-[85%]',
                  message.role === 'user' && 'ml-auto flex-row-reverse',
                )}
              >
                <div
                  className={cn(
                    'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border',
                    message.role === 'user'
                      ? 'border-secondary/50 bg-secondary/10 text-secondary'
                      : 'border-primary/50 bg-primary/10 text-primary',
                  )}
                >
                  {message.role === 'user' ? (
                    <User className="h-3.5 w-3.5" />
                  ) : (
                    <Bot className="h-3.5 w-3.5" />
                  )}
                </div>
                {/* Gradient-accented bubbles distinguish user (violet) from assistant
                    (cyan) per §5, replacing the old flat slate-800/900 fill. */}
                <div
                  className={cn(
                    'min-w-0 rounded-lg border px-3 py-2 text-sm text-text backdrop-blur-md',
                    message.role === 'user'
                      ? 'border-secondary/30 bg-gradient-to-br from-secondary/15 to-secondary/5'
                      : 'border-primary/30 bg-gradient-to-br from-primary/15 to-primary/5',
                  )}
                >
                  {message.role === 'assistant' && (
                    <p className="mb-1 font-mono text-xs uppercase tracking-wide text-text-muted">
                      {message.usedLlm ? `Reasoned (${message.provider ?? 'LLM'})` : 'Direct command'}
                    </p>
                  )}
                  <p className="whitespace-pre-wrap break-words">{message.text}</p>
                  {message.toolCalls?.map((call, callIndex) => (
                    <p
                      key={callIndex}
                      className={cn(
                        'mt-1 break-words font-mono text-xs',
                        isCapabilityRejection(call)
                          ? 'text-warning'
                          : call.result.success
                            ? 'text-success'
                            : 'text-danger',
                      )}
                    >
                      {call.tool_name}: {call.result.success ? 'ok' : call.result.error}
                    </p>
                  ))}
                </div>
              </motion.div>
            ))}

            {/* Typing/thinking indicator (§5) while awaiting the assistant's reply --
                three bouncing dots in a bubble shaped like an assistant message. */}
            {sending && (
              <motion.div
                key="typing-indicator"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="flex max-w-[92%] items-center gap-2 sm:max-w-[85%]"
              >
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-primary/50 bg-primary/10 text-primary">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="flex items-center gap-1 rounded-lg border border-primary/30 bg-gradient-to-br from-primary/15 to-primary/5 px-3 py-2.5 backdrop-blur-md">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary" />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {error && <p className="mt-3 text-sm text-danger">{error}</p>}

        <form onSubmit={handleSubmit} className="mt-4 flex gap-2">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message Jarvis…"
            className="flex-1"
          />
          <Button type="submit" disabled={sending || !input.trim()} loading={sending}>
            <Send className="h-4 w-4" />
            Send
          </Button>
        </form>
      </div>
    </main>
  )
}
