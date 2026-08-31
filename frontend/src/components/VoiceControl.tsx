import { Check, Loader2, Mic, Send, X } from 'lucide-react'
import { useVoiceInput } from '../hooks/useVoiceInput'
import { cn } from './ui/utils'

/** Push-to-talk widget (file 10 prompt 2, §37 Phase 9): hold the mic button (or the
 * backtick hotkey -- see useVoiceInput) to record, review/edit the transcript before
 * anything runs, then send. Floats over every tab so it's reachable regardless of
 * which page is open, without disturbing any page's own layout.
 *
 * Restyled per md-files/ui-development.md §3 as the visual "core" of the HUD -- the
 * assistant's signature interaction gets a pulsing neon ring while listening, using
 * the same glow tokens as the rest of the theme rather than a plain flat FAB.
 */
export function VoiceControl() {
  const {
    status,
    transcript,
    setTranscript,
    error,
    lastResponse,
    needsConfirmation,
    startRecording,
    stopRecording,
    confirmAndSend,
    cancel,
  } = useVoiceInput()

  const isRecording = status === 'recording'
  const isBusy = status === 'transcribing' || status === 'sending'

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-2">
      {error && (
        <div className="max-w-sm rounded-lg border border-danger/50 bg-surface/90 px-3 py-2 text-sm text-danger backdrop-blur-md">
          {error}
        </div>
      )}

      {lastResponse && status === 'idle' && (
        <div className="max-w-sm rounded-lg border border-border bg-surface/90 px-3 py-2 text-sm text-text backdrop-blur-md">
          <p className="text-xs uppercase tracking-wide text-text-muted">
            {lastResponse.used_llm ? `Reasoned (${lastResponse.provider ?? 'LLM'})` : 'Direct command'}
          </p>
          <p>{lastResponse.text}</p>
          {needsConfirmation && (
            <button
              onClick={() => {
                setTranscript(lastResponse.transcript)
                void confirmAndSend({ confirmed: true })
              }}
              className="mt-2 flex items-center gap-1.5 rounded-md border border-warning/50 bg-warning/10 px-2 py-1 text-xs font-medium text-warning transition-colors hover:bg-warning/20"
            >
              <Check className="h-3.5 w-3.5" />
              Confirm & resend
            </button>
          )}
        </div>
      )}

      {status === 'reviewing' && (
        <div className="w-80 rounded-lg border border-primary/40 bg-surface/90 p-3 text-sm text-text shadow-glow-primary backdrop-blur-md">
          <p className="mb-1 text-xs uppercase tracking-wide text-text-muted">
            Confirm what you said
          </p>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={2}
            className="w-full rounded-md border border-border bg-bg px-2 py-1 text-text focus:border-primary focus:outline-none focus:shadow-glow-primary"
            autoFocus
          />
          <div className="mt-2 flex justify-end gap-2">
            <button
              onClick={cancel}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-text-muted transition-colors hover:text-text"
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </button>
            <button
              onClick={() => void confirmAndSend()}
              disabled={!transcript.trim()}
              className="flex items-center gap-1 rounded-md border border-primary/60 bg-primary px-3 py-1 text-xs font-medium text-bg transition-all hover:shadow-glow-primary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" />
              Send
            </button>
          </div>
        </div>
      )}

      <div className="relative flex h-14 w-14 items-center justify-center">
        {/* Pulsing neon rings -- the HUD "core" -- only animate while actively
            listening; `prefers-reduced-motion` is handled globally in index.css. */}
        {isRecording && (
          <>
            <span className="absolute inset-0 animate-ping rounded-full bg-danger/40" />
            <span className="absolute -inset-1.5 animate-pulse rounded-full border border-danger/60" />
          </>
        )}
        <button
          onPointerDown={startRecording}
          onPointerUp={stopRecording}
          onPointerLeave={() => isRecording && stopRecording()}
          disabled={isBusy || status === 'reviewing'}
          title="Hold to talk (or hold `)"
          className={cn(
            'relative flex h-14 w-14 items-center justify-center rounded-full border text-white shadow-lg transition-all duration-200 disabled:opacity-50',
            isRecording
              ? 'border-danger/60 bg-danger shadow-[0_0_20px_4px_color-mix(in_srgb,var(--color-danger)_55%,transparent)]'
              : 'border-primary/50 bg-surface text-primary hover:shadow-glow-primary',
          )}
        >
          {isBusy ? (
            <Loader2 className="h-6 w-6 animate-spin" />
          ) : (
            <Mic className="h-6 w-6" />
          )}
        </button>
      </div>
      <span className="rounded-md bg-surface/70 px-2 py-0.5 text-xs text-text-muted backdrop-blur-md">
        {isRecording ? 'Recording…' : status === 'transcribing' ? 'Transcribing…' : status === 'sending' ? 'Sending…' : 'Hold to talk'}
      </span>
    </div>
  )
}
