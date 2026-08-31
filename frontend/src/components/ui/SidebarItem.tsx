import type { ComponentType } from 'react'
import { cn } from './utils'

interface SidebarItemProps {
  icon: ComponentType<{ className?: string }>
  label: string
  active?: boolean
  collapsed?: boolean
  onClick?: () => void
}

/** One nav entry for `Sidebar` -- icon + label, with a glowing gradient underline
 * sweep marking the active item (md-files/ui-development.md §2/§3; glow via
 * box-shadow, not filter, per the perf guidance). Pass the same `collapsed` state as
 * the parent `Sidebar` so the label hides and only the icon remains in the rail.
 *
 * Usage: <SidebarItem icon={MessageSquare} label="Chat" active={view === 'chat'} onClick={() => setView('chat')} />
 */
export function SidebarItem({ icon: Icon, label, active = false, collapsed = false, onClick }: SidebarItemProps) {
  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-200',
        collapsed && 'justify-center px-0',
        active ? 'text-primary' : 'text-text-muted hover:bg-surface-2 hover:text-text',
      )}
    >
      <Icon className="h-5 w-5 shrink-0" />
      {!collapsed && <span>{label}</span>}
      {active && (
        <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-gradient-to-r from-primary to-secondary shadow-[0_0_8px_1px_var(--color-primary)]" />
      )}
    </button>
  )
}
