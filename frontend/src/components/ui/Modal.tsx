import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { X } from 'lucide-react'
import { cn } from './utils'

interface ModalProps {
  open: boolean
  onClose: () => void
  title?: string
  children?: ReactNode
  className?: string
}

/** Portal-rendered dialog with a backdrop, Escape-to-close, and a fade/scale entrance
 * (md-files/ui-development.md §2/§4). Generic container -- `ConfirmDialog` builds a
 * yes/no confirmation on top of this for the `window.confirm()` call sites in
 * Tasks.tsx, Routines.tsx, and Settings.tsx.
 *
 * Usage: <Modal open={open} onClose={() => setOpen(false)} title="Edit task">...</Modal>
 */
export function Modal({ open, onClose, title, children, className }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-4 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          onClick={onClose}
          role="presentation"
        >
          <motion.div
            className={cn(
              'w-full max-w-md rounded-lg border border-border bg-surface/90 p-5 shadow-2xl backdrop-blur-md',
              className,
            )}
            initial={{ opacity: 0, scale: 0.95, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 8 }}
            transition={{ duration: 0.2 }}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label={title}
          >
            {title && (
              <div className="mb-4 flex items-center justify-between">
                <h2 className="font-display text-h3 text-text">{title}</h2>
                <button
                  onClick={onClose}
                  aria-label="Close"
                  className="rounded p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            )}
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
