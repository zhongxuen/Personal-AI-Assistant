import type { ButtonVariant } from './Button'
import { Button } from './Button'
import { Modal } from './Modal'

interface ConfirmDialogProps {
  open: boolean
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  /** Use 'destructive' for delete/remove-style confirmations. */
  confirmVariant?: ButtonVariant
  loading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** Themed yes/no confirmation, replacing the native `window.confirm()` call sites
 * (Tasks.tsx:117, Routines.tsx:219, Settings.tsx:90 as of md-files/ui-development.md
 * §1/§4).
 *
 * Usage:
 *   <ConfirmDialog
 *     open={pendingDeleteId !== null}
 *     message="Delete this task? This can't be undone."
 *     confirmVariant="destructive"
 *     onConfirm={() => remove(pendingDeleteId!)}
 *     onCancel={() => setPendingDeleteId(null)}
 *   />
 */
export function ConfirmDialog({
  open,
  title = 'Are you sure?',
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmVariant = 'primary',
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <p className="text-sm text-text-muted">{message}</p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button variant={confirmVariant} onClick={onConfirm} loading={loading}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  )
}
