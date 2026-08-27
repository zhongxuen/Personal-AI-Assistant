import { useCallback, useEffect, useState } from 'react'
import { getDiscordStatus, startDiscordBot, stopDiscordBot } from '../services/api'
import type { DiscordStatus } from '../types/discord'

// Matches useLlmUsage.ts's poll cadence for the steady "connected"/"stopped"/"disabled"
// states -- but while "starting" (the client is still handshaking with Discord) polls
// every 2s instead, so the Settings panel flips to "connected" quickly after pressing
// Start rather than waiting up to 15s for the next slow tick.
const STEADY_POLL_INTERVAL_MS = 15_000
const STARTING_POLL_INTERVAL_MS = 2_000

interface UseDiscordBotResult {
  status: DiscordStatus | null
  loading: boolean
  error: string | null
  starting: boolean
  stopping: boolean
  refresh: () => void
  start: () => Promise<void>
  stop: () => Promise<void>
}

/** Loads `/api/discord/status` and keeps re-polling it so the Settings panel reflects
 * the bot actually connecting/disconnecting -- both can happen without this tab having
 * triggered them (e.g. the backend restarting, or another browser tab calling
 * start/stop), same rationale as useLlmUsage.ts's own poll.
 */
export function useDiscordBot(): UseDiscordBotResult {
  const [status, setStatus] = useState<DiscordStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    let timeout: ReturnType<typeof setTimeout>

    function scheduleNext(nextStatus: DiscordStatus | null) {
      if (cancelled) return
      const interval = nextStatus?.state === 'starting' ? STARTING_POLL_INTERVAL_MS : STEADY_POLL_INTERVAL_MS
      timeout = setTimeout(load, interval)
    }

    function load() {
      getDiscordStatus()
        .then((response) => {
          if (cancelled) return
          setStatus(response)
          setError(null)
          scheduleNext(response)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setError(err instanceof Error ? err.message : 'Unknown error')
          scheduleNext(null)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }

    setLoading(true)
    load()
    return () => {
      cancelled = true
      clearTimeout(timeout)
    }
  }, [version])

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const next = await startDiscordBot()
      setStatus(next)
      setError(null)
    } finally {
      setStarting(false)
      refresh() // pick up the faster "starting" poll cadence right away
    }
  }, [refresh])

  const stop = useCallback(async () => {
    setStopping(true)
    try {
      const next = await stopDiscordBot()
      setStatus(next)
      setError(null)
    } finally {
      setStopping(false)
      refresh()
    }
  }, [refresh])

  return { status, loading, error, starting, stopping, refresh, start, stop }
}
