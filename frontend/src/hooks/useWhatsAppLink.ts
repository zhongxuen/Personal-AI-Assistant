import { useCallback, useEffect, useState } from 'react'
import {
  createWhatsAppLinkCode,
  getWhatsAppLinkStatus,
  unlinkWhatsApp,
} from '../services/api'
import type { WhatsAppLinkCode, WhatsAppLinkStatus } from '../types/whatsapp'

// Polled only while a pairing code is outstanding. The linking event happens *outside*
// this tab -- the user sends the code from their phone, and the webhook
// (app/api/routes/whatsapp_webhook.py) links the number server-side -- so there is no
// local action to hang the "you're linked now" update off. Once no code is outstanding
// the status can't change without this tab doing it, so polling stops entirely rather
// than idling like useDiscordBot's steady tick (the bot really can go down on its own;
// a linked number can't).
const PAIRING_POLL_INTERVAL_MS = 5_000

// Upper bound on how long that poll runs, measured on *this* clock. It matches the
// backend's LINK_CODE_TTL_MINUTES with a minute of slack, and exists because the
// backend's `code_expires_at` can't be compared against the browser's clock: it's a
// naive local datetime (`datetime.now()`), so a UTC-hosted backend and a UTC+8 browser
// disagree by hours about whether the same code is still alive. An expired code also
// stays `code_pending` server-side (it's only cleared when something tries to use it),
// so without this bound the poll would never stop.
const PAIRING_POLL_MAX_MS = 16 * 60 * 1_000

interface UseWhatsAppLinkResult {
  status: WhatsAppLinkStatus | null
  /** The code from this tab's own `generate()` call, shown once. Null after it's
   * consumed, replaced, or on any reload -- the backend never hands it out twice. */
  code: WhatsAppLinkCode | null
  /** When that code runs out, as a local `Date.now()`-comparable timestamp derived from
   * `expires_in_seconds` rather than from the backend's wall-clock string -- see
   * `PAIRING_POLL_MAX_MS` above on why the string can't be trusted across machines. */
  codeExpiresAtMs: number | null
  loading: boolean
  error: string | null
  generating: boolean
  unlinking: boolean
  generate: () => Promise<void>
  unlink: () => Promise<void>
}

/** Loads `GET /api/whatsapp/link` and, while a code is outstanding, keeps re-polling it
 * so the panel flips to "Linked" on its own once the user sends the code from their
 * phone. `generate`/`unlink` wrap the other two routes.
 */
export function useWhatsAppLink(): UseWhatsAppLinkResult {
  const [status, setStatus] = useState<WhatsAppLinkStatus | null>(null)
  const [code, setCode] = useState<WhatsAppLinkCode | null>(null)
  const [codeExpiresAtMs, setCodeExpiresAtMs] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [generating, setGenerating] = useState(false)
  const [unlinking, setUnlinking] = useState(false)

  const refresh = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let cancelled = false
    let timeout: ReturnType<typeof setTimeout>
    const pollUntil = Date.now() + PAIRING_POLL_MAX_MS

    function scheduleNext(nextStatus: WhatsAppLinkStatus | null) {
      if (cancelled) return
      if (!nextStatus?.code_pending) return // nothing to wait for
      if (Date.now() >= pollUntil) return // the code can only be dead by now
      timeout = setTimeout(load, PAIRING_POLL_INTERVAL_MS)
    }

    function load() {
      getWhatsAppLinkStatus()
        .then((response) => {
          if (cancelled) return
          setStatus(response)
          setError(null)
          // The code we're holding is gone server-side -- consumed by the pairing
          // message, or replaced. Stop showing a string that no longer works.
          if (!response.code_pending) {
            setCode(null)
            setCodeExpiresAtMs(null)
          }
          scheduleNext(response)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setError(err instanceof Error ? err.message : 'Unknown error')
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

  const generate = useCallback(async () => {
    setGenerating(true)
    try {
      const next = await createWhatsAppLinkCode()
      setCode(next)
      setCodeExpiresAtMs(Date.now() + next.expires_in_seconds * 1_000)
      setError(null)
    } finally {
      setGenerating(false)
      refresh() // start the pairing poll so linking shows up without a manual reload
    }
  }, [refresh])

  const unlink = useCallback(async () => {
    setUnlinking(true)
    try {
      await unlinkWhatsApp()
      setCode(null)
      setCodeExpiresAtMs(null)
      setError(null)
    } finally {
      setUnlinking(false)
      refresh()
    }
  }, [refresh])

  return {
    status,
    code,
    codeExpiresAtMs,
    loading,
    error,
    generating,
    unlinking,
    generate,
    unlink,
  }
}
