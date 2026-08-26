import { useCallback, useEffect, useRef, useState } from 'react'
import { sendVoiceText, transcribeVoiceAudio } from '../services/api'
import type { VoiceMessageResponse } from '../types/voice'

export type VoiceStatus = 'idle' | 'recording' | 'transcribing' | 'reviewing' | 'sending' | 'error'

interface UseVoiceInputResult {
  status: VoiceStatus
  /** The (editable) transcript shown to the user during 'reviewing' -- see module docstring
   * in backend/app/api/routes/voice.py for why review happens before anything executes. */
  transcript: string
  setTranscript: (value: string) => void
  error: string | null
  lastResponse: VoiceMessageResponse | null
  /** True when the last send was denied because a CONFIRM-level tool needs an explicit
   * confirmed=true retry (§19) -- lets the UI offer a "confirm & resend" action. */
  needsConfirmation: boolean
  startRecording: () => void
  stopRecording: () => void
  confirmAndSend: (opts?: { confirmed?: boolean }) => Promise<void>
  cancel: () => void
}

// Hold this key to record, mirroring the mic button's press-and-hold gesture (a
// Discord-style push-to-talk hotkey). Ignored while typing in an input/textarea so a
// literal backtick elsewhere on the page doesn't start a recording by accident.
const PUSH_TO_TALK_KEY = '`'

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable
}

/** Push-to-talk capture + review + submit, driving the same POST /api/voice/message
 * endpoint the whole way (backend/app/api/routes/voice.py): record -> dry-run
 * transcribe -> user reviews/edits the transcript -> real submit. Recording, STT
 * preview, and final submission are three distinct states so the caller never has to
 * guess what's in flight.
 */
export function useVoiceInput(): UseVoiceInputResult {
  const [status, setStatus] = useState<VoiceStatus>('idle')
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [lastResponse, setLastResponse] = useState<VoiceMessageResponse | null>(null)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)

  const needsConfirmation =
    lastResponse?.tool_calls.some(
      (call) => !call.result.success && call.result.error?.startsWith('Permission denied: CONFIRM'),
    ) ?? false

  const cleanupStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
  }, [])

  const transcribeAndReview = useCallback(async (blob: Blob) => {
    setStatus('transcribing')
    try {
      const response = await transcribeVoiceAudio(blob)
      setTranscript(response.transcript)
      setStatus('reviewing')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Transcription failed.')
      setStatus('error')
    }
  }, [])

  const startRecording = useCallback(() => {
    if (status === 'recording') return
    setError(null)
    setLastResponse(null)
    void (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        streamRef.current = stream
        chunksRef.current = []
        const recorder = new MediaRecorder(stream)
        recorder.ondataavailable = (event) => {
          if (event.data.size > 0) chunksRef.current.push(event.data)
        }
        recorder.onstop = () => {
          cleanupStream()
          const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
          void transcribeAndReview(blob)
        }
        mediaRecorderRef.current = recorder
        recorder.start()
        setStatus('recording')
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Microphone access failed.')
        setStatus('error')
      }
    })()
  }, [status, cleanupStream, transcribeAndReview])

  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
    }
  }, [])

  const confirmAndSend = useCallback(
    async (opts: { confirmed?: boolean } = {}) => {
      if (!transcript.trim()) return
      setStatus('sending')
      setError(null)
      try {
        const response = await sendVoiceText(transcript.trim(), { confirmed: opts.confirmed })
        setLastResponse(response)
        if (response.audio_base64) {
          const audio = new Audio(`data:audio/wav;base64,${response.audio_base64}`)
          void audio.play().catch(() => {
            // Autoplay can be blocked by the browser -- the text response is still
            // shown, so a blocked playback is non-fatal.
          })
        }
        setStatus('idle')
        setTranscript('')
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Sending failed.')
        setStatus('error')
      }
    },
    [transcript],
  )

  const cancel = useCallback(() => {
    setTranscript('')
    setLastResponse(null)
    setError(null)
    setStatus('idle')
  }, [])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== PUSH_TO_TALK_KEY || event.repeat || isEditableTarget(event.target)) return
      event.preventDefault()
      startRecording()
    }
    function onKeyUp(event: KeyboardEvent) {
      if (event.key !== PUSH_TO_TALK_KEY) return
      stopRecording()
    }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [startRecording, stopRecording])

  // Release the microphone if the component unmounts mid-recording.
  useEffect(() => cleanupStream, [cleanupStream])

  return {
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
  }
}
