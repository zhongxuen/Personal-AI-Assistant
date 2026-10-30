import { useState } from 'react'
import { HardDrive, X } from 'lucide-react'
import type { StorageMode } from '../types/health'
import { Badge, Panel } from './ui'

const DISMISS_KEY = 'jarvis:storage-banner-dismissed'

/** The cross-device limitation, worded once so the Settings panel and the chat banner
 * can never drift apart. Mirrors README.md / docs/deployment.md "Known limitations". */
function NoticeText() {
  return (
    <>
      <p>
        Your conversations, memory and files are stored <strong>only on the device running the
        JARVIS backend</strong>. Nothing is synced to the cloud or kept online, and nothing is
        retained by the AI provider.
      </p>
      <p className="mt-2">
        Using JARVIS from another device through the deployed site talks to a{' '}
        <strong>temporary server</strong>: its conversations, memory and files are wiped whenever
        it restarts or redeploys, and they won't appear on your desktop install.
      </p>
    </>
  )
}

/** Settings page panel: always shown, with which kind of backend this tab is talking to. */
export function StorageNoticePanel({ mode }: { mode: StorageMode | null }) {
  return (
    <Panel className="mt-8 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 font-display text-sm font-semibold text-text">
          <HardDrive className="h-4 w-4 text-primary" />
          Where your data lives
        </h2>
        {mode === 'local-durable' && <Badge tone="success">This backend: local, kept across restarts</Badge>}
        {mode === 'ephemeral' && <Badge tone="warning">This backend: temporary server</Badge>}
      </div>
      <div className="mt-3 text-sm text-text-muted">
        <NoticeText />
      </div>
    </Panel>
  )
}

function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISS_KEY) === '1'
  } catch {
    // Storage disabled (private mode, blocked site data) -- just show the banner.
    return false
  }
}

/** Chat banner, shown only when this tab is talking to the ephemeral (deployed)
 * backend. Dismissal is remembered per browser; if storage is unavailable the banner
 * simply reappears next visit, which errs on the side of telling the user. */
export function EphemeralStorageBanner({ mode }: { mode: StorageMode | null }) {
  const [dismissed, setDismissed] = useState(readDismissed)

  if (mode !== 'ephemeral' || dismissed) return null

  function dismiss() {
    setDismissed(true)
    try {
      window.localStorage.setItem(DISMISS_KEY, '1')
    } catch {
      // Not persisted -- it's still hidden for this page load.
    }
  }

  return (
    <div
      role="status"
      className="mt-3 flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-text-muted"
    >
      <HardDrive className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
      <div className="flex-1">
        <NoticeText />
      </div>
      <button
        onClick={dismiss}
        aria-label="Dismiss storage notice"
        className="rounded p-1 text-text-muted transition-colors hover:text-text"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
