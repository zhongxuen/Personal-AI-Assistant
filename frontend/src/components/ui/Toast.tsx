import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { cn } from './utils'

export type ToastTone = 'success' | 'warning' | 'danger' | 'info'

interface ToastItem {
  id: number
  tone: ToastTone
  message: string
}

interface ToastContextValue {
  show: (message: string, tone?: ToastTone) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const TONE_ICON: Record<ToastTone, typeof Info> = {
  success: CheckCircle2,
  warning: AlertTriangle,
  danger: XCircle,
  info: Info,
}

const TONE_STYLES: Record<ToastTone, string> = {
  success: 'border-success/60 text-success',
  warning: 'border-warning/60 text-warning',
  danger: 'border-danger/60 text-danger',
  info: 'border-primary/60 text-primary',
}

const AUTO_DISMISS_MS = 4000

/** Hook for firing toast notifications from anywhere under `ToastProvider`. Throws if
 * called outside the provider so a missing mount fails loudly instead of silently
 * dropping notifications.
 *
 * Usage: const { show } = useToast(); show('Settings saved', 'success')
 */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within a ToastProvider')
  return ctx
}

/** Slide-in, auto-dismissing toast notifications (md-files/ui-development.md §2/§4),
 * replacing native `window.confirm()`/`alert()` and silent success across the app.
 * Mount once near the root (e.g. wrapping `<App />`) and call `useToast().show(...)`
 * from any descendant.
 *
 * Usage: <ToastProvider><App /></ToastProvider>
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const show = useCallback(
    (message: string, tone: ToastTone = 'info') => {
      const id = Date.now() + Math.random()
      setToasts((prev) => [...prev, { id, tone, message }])
      window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
    },
    [dismiss],
  )

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-6 left-1/2 z-[60] flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4">
        <AnimatePresence>
          {toasts.map((toast) => {
            const Icon = TONE_ICON[toast.tone]
            return (
              <motion.div
                key={toast.id}
                initial={{ opacity: 0, y: 16, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -8, scale: 0.95 }}
                transition={{ duration: 0.2 }}
                className={cn(
                  'pointer-events-auto flex items-center gap-2 rounded-lg border bg-surface/95 px-3 py-2 text-sm text-text shadow-lg backdrop-blur-md',
                  TONE_STYLES[toast.tone],
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="flex-1">{toast.message}</span>
                <button onClick={() => dismiss(toast.id)} aria-label="Dismiss" className="text-text-muted hover:text-text">
                  <X className="h-3.5 w-3.5" />
                </button>
              </motion.div>
            )
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  )
}
