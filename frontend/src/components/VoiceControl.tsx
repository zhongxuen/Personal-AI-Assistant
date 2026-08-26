import { useVoiceInput } from '../hooks/useVoiceInput'

/** Push-to-talk widget (file 10 prompt 2, §37 Phase 9): hold the mic button (or the
 * backtick hotkey -- see useVoiceInput) to record, review/edit the transcript before
 * anything runs, then send. Floats over every tab so it's reachable regardless of
 * which page is open, without disturbing any page's own layout.
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
        <div className="max-w-sm rounded border border-red-800 bg-red-950 px-3 py-2 text-sm text-red-200">
          {error}
        </div>
      )}

      {lastResponse && status === 'idle' && (
        <div className="max-w-sm rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200">
          <p className="text-xs uppercase tracking-wide text-slate-500">
            {lastResponse.used_llm ? `Reasoned (${lastResponse.provider ?? 'LLM'})` : 'Direct command'}
          </p>
          <p>{lastResponse.text}</p>
          {needsConfirmation && (
            <button
              onClick={() => {
                setTranscript(lastResponse.transcript)
                void confirmAndSend({ confirmed: true })
              }}
              className="mt-2 rounded bg-amber-700 px-2 py-1 text-xs font-medium text-white hover:bg-amber-600"
            >
              Confirm & resend
            </button>
          )}
        </div>
      )}

      {status === 'reviewing' && (
        <div className="w-80 rounded border border-slate-700 bg-slate-900 p-3 text-sm text-slate-200 shadow-lg">
          <p className="mb-1 text-xs uppercase tracking-wide text-slate-500">
            Confirm what you said
          </p>
          <textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            rows={2}
            className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-100 focus:border-slate-500 focus:outline-none"
            autoFocus
          />
          <div className="mt-2 flex justify-end gap-2">
            <button
              onClick={cancel}
              className="rounded px-2 py-1 text-xs font-medium text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
            <button
              onClick={() => void confirmAndSend()}
              disabled={!transcript.trim()}
              className="rounded bg-slate-700 px-3 py-1 text-xs font-medium text-white hover:bg-slate-600 disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </div>
      )}

      <button
        onPointerDown={startRecording}
        onPointerUp={stopRecording}
        onPointerLeave={() => isRecording && stopRecording()}
        disabled={isBusy || status === 'reviewing'}
        title="Hold to talk (or hold `)"
        className={`flex h-14 w-14 items-center justify-center rounded-full text-white shadow-lg transition disabled:opacity-50 ${
          isRecording ? 'animate-pulse bg-red-600' : 'bg-slate-700 hover:bg-slate-600'
        }`}
      >
        {isBusy ? (
          <span className="text-xs">{status === 'transcribing' ? '...' : 'Send'}</span>
        ) : (
          <MicIcon />
        )}
      </button>
      <span className="text-xs text-slate-500">
        {isRecording ? 'Recording…' : status === 'transcribing' ? 'Transcribing…' : status === 'sending' ? 'Sending…' : 'Hold to talk'}
      </span>
    </div>
  )
}

function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-6 w-6">
      <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z" />
      <path strokeLinecap="round" d="M19 11a7 7 0 0 1-14 0M12 18v3" />
    </svg>
  )
}
