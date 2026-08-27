import type { ReactNode } from 'react'
import { ChevronsLeft, ChevronsRight } from 'lucide-react'
import { cn } from './utils'

interface SidebarProps {
  collapsed: boolean
  onToggleCollapsed: () => void
  header?: ReactNode
  children?: ReactNode
  className?: string
}

/** Collapsible left navigation rail (md-files/ui-development.md §3), the planned
 * replacement for the inline top tab strip in App.tsx. Collapsed state is controlled
 * by the caller so App.tsx can persist it / drive it from a breakpoint or an
 * off-canvas mobile drawer -- this component just renders the two widths and the
 * toggle affordance. Compose with `SidebarItem` children for each nav entry.
 *
 * Usage:
 *   <Sidebar collapsed={collapsed} onToggleCollapsed={() => setCollapsed((c) => !c)}>
 *     <SidebarItem icon={MessageSquare} label="Chat" active={view === 'chat'} onClick={...} />
 *   </Sidebar>
 */
export function Sidebar({ collapsed, onToggleCollapsed, header, children, className }: SidebarProps) {
  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-border bg-surface/70 backdrop-blur-md transition-[width] duration-300',
        collapsed ? 'w-16' : 'w-56',
        className,
      )}
    >
      {header && <div className={cn('border-b border-border p-3', collapsed && 'px-2')}>{header}</div>}
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-2">{children}</nav>
      <button
        onClick={onToggleCollapsed}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className="flex items-center justify-center gap-2 border-t border-border p-3 text-text-muted transition-colors hover:text-primary"
      >
        {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
      </button>
    </aside>
  )
}
