import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Bot, Send, User } from 'lucide-react'
import { streamChatMessage } from '../services/api'
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
  /** True while this reply is still arriving over the stream. Drives the in-bubble
   * thinking dots and suppresses the "Reasoned (provider)" label, which isn't known
   * until the terminal `done` event says which provider actually answered. Never
   * persisted as true -- see the rehydration note in the component below. */
  streaming?: boolean
}

/** A tool call whose result is a platform-capability rejection (§22) -- ToolExecutor's
 * `"This action isn't available on <platform>."` (backend/app/core/tool_executor.py).
 * Flagged separately from an ordinary tool failure only so it can render with a
 * distinct, expected-not-alarming style instead of looking like an error.
 */
function isCapabilityRejection(call: AssistantToolCallResult): boolean {
  return !call.result.success && (call.result.error?.includes("isn't available on") ?? false)
}

/** Web chat (§37 Phase 11, file 12 prompt 2) -- POSTs platform="web" to
 * /api/assistant/stream (see services/api.ts's streamChatMessage), the Server-Sent
 * Events sibling of the /api/assistant/message endpoint desktop/voice use. Same
 * AssistantCore, same tools, same final answer; the reply just arrives in pieces so it
 * can be rendered while it's still being generated, instead of the whole turn elapsing
 * behind a typing indicator.
 *
 * So a message asking for a desktop-only tool (e.g. "open vscode") still comes back
 * with the same §22 explanatory rejection any other platform-capability check
 * produces, not a silent no-op or an actual attempt to control this machine -- there's
 * nothing web-specific to gate here beyond sending the message and rendering whatever
 * AssistantCore actually returns.
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

  // `messages` is persisted, so a reload (or Chrome discarding the tab) partway through
  // a streamed reply would rehydrate a bubble still flagged `streaming` -- with no
  // stream left to finish it, it would sit on the thinking dots forever. The in-flight
  // request didn't survive the reload either way, so on mount any half-written reply is
  // dropped and any flag left set is cleared.
  useEffect(() => {
    setMessages((prev) => {
      if (!prev.some((message) => message.streaming)) return prev
      return prev
        .filter((message) => !(message.streaming && !message.text))
        .map((message) => (message.streaming ? { ...message, streaming: false } : message))
    })
    // Mount-only: this repairs state restored from localStorage once, it isn't an
    // invariant to maintain on every render. `setMessages` is stable (memoized in
    // usePersistentState), so listing it doesn't reintroduce a re-run per render.
  }, [setMessages])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    setMessages((prev) => [...prev, { role: 'user', text }])
    setInput('')
    setSending(true)
    setError(null)

    // The reply is streamed into this one placeholder bubble, appended up front and
    // then mutated in place, so text appears as it's generated instead of after the
    // whole turn completes. Its index is fixed the moment it's appended -- nothing else
    // can append to `messages` while a send is in flight (the composer is disabled on
    // `sending`), so a positional update is safe here.
    let assistantIndex = -1
    setMessages((prev) => {
      assistantIndex = prev.length
      return [...prev, { role: 'assistant', text: '', streaming: true }]
    })

    const updateAssistant = (patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((message, index) =>
          index === assistantIndex ? { ...message, ...patch } : message,
        ),
      )
    }

    try {
      let streamed = ''
      await streamChatMessage(text, conversationId, (event) => {
        if (event.type === 'delta') {
          streamed += event.text
          updateAssistant({ text: streamed })
        } else if (event.type === 'tool' && event.tool_call) {
          // Show each tool call the moment it finishes rather than all at once at the
          // end -- on a turn that runs several, this is the only progress the user gets.
          const call = event.tool_call
          setMessages((prev) =>
            prev.map((message, index) =>
              index === assistantIndex
                ? { ...message, toolCalls: [...(message.toolCalls ?? []), call] }
                : message,
            ),
          )
        } else if (event.type === 'done' && event.response) {
          // `done` is authoritative -- replace the accumulated preview with it rather
          // than keeping whatever the deltas happened to add up to. On a tool-only turn
          // there were no deltas at all and this is the entire reply.
          updateAssistant({
            text: event.response.text,
            usedLlm: event.response.used_llm,
            provider: event.response.provider,
            toolCalls: event.response.tool_calls,
            streaming: false,
          })
        }
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message.')
      // Drop the placeholder rather than leave an empty assistant bubble behind -- the
      // error is reported below the thread, and a blank bubble reads as a silent
      // non-answer.
      setMessages((prev) => prev.filter((_, index) => index !== assistantIndex))
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
                  {/* Which path answered isn't known until the stream's terminal event,
                      so the label is held back rather than guessed at and corrected. */}
                  {message.role === 'assistant' && !message.streaming && (
                    <p className="mb-1 font-mono text-xs uppercase tracking-wide text-text-muted">
                      {message.usedLlm ? `Reasoned (${message.provider ?? 'LLM'})` : 'Direct command'}
                    </p>
                  )}
                  {message.streaming && !message.text ? (
                    /* Thinking dots live inside the reply bubble now, so the bubble
                       appears immediately and fills with text in place -- rather than a
                       separate indicator that vanishes and is replaced by a different
                       element once the reply lands. */
                    <span className="flex items-center gap-1 py-1">
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
                      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary" />
                    </span>
                  ) : (
                    <p className="whitespace-pre-wrap break-words">
                      {message.text}
                      {/* A blinking caret while text is still arriving, so a pause
                          between chunks reads as "still generating" rather than "done". */}
                      {message.streaming && (
                        <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary/70 align-text-bottom" />
                      )}
                    </p>
                  )}
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

            {/* The typing/thinking indicator (§5) that used to live here is now rendered
                inside the assistant bubble itself -- the bubble is appended as soon as
                the message is sent and fills with streamed text in place, so a separate
                placeholder element would only flicker in and out ahead of it. */}
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
