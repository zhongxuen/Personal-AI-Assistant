import { useState } from 'react'
import type { ComponentType } from 'react'
import {
  Activity,
  Cpu,
  History,
  ListChecks,
  LogOut,
  Menu,
  MessageSquare,
  Repeat,
  Settings as SettingsIcon,
} from 'lucide-react'
import { StatusPage } from './pages/StatusPage'
import { TasksPage } from './pages/Tasks'
import { RoutinesPage } from './pages/Routines'
import { ProviderStatusPage } from './pages/ProviderStatus'
import { ActivityPage } from './pages/Activity'
import { SettingsPage } from './pages/Settings'
import { ChatPage } from './pages/Chat'
import { LoginPage } from './pages/Login'
import { VoiceControl } from './components/VoiceControl'
import { LimitBar } from './components/LimitBar'
import { Sidebar, SidebarItem } from './components/ui'
import { cn } from './components/ui/utils'
import { useAuth } from './hooks/useAuth'
import { useLlmUsage } from './hooks/useLlmUsage'
import { useMediaQuery } from './hooks/useMediaQuery'
import { usePersistentState } from './hooks/usePersistentState'

type View = 'chat' | 'status' | 'tasks' | 'routines' | 'providers' | 'activity' | 'settings'

const NAV_ITEMS: { id: View; label: string; icon: ComponentType<{ className?: string }> }[] = [
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'tasks', label: 'Tasks', icon: ListChecks },
  { id: 'routines', label: 'Routines', icon: Repeat },
  { id: 'providers', label: 'AI Providers', icon: Cpu },
  { id: 'activity', label: 'Activity', icon: History },
  { id: 'settings', label: 'Settings', icon: SettingsIcon },
  { id: 'status', label: 'Status', icon: Activity },
]

const VALID_VIEWS = new Set<View>(NAV_ITEMS.map((item) => item.id))

function App() {
  // Persisted (§ user report) so a refresh -- or Chrome discarding this tab in the
  // background and reloading it fresh next time it's shown -- lands back on whatever
  // tab the user was actually on instead of always resetting to Chat. Guarded against
  // a stale value from a previous build that removed/renamed a nav item.
  const [view, setView] = usePersistentState<View>('jarvis:view', 'chat')
  const safeView = VALID_VIEWS.has(view) ? view : 'chat'
  // Desktop rail collapse (icon-only) and the separate mobile off-canvas drawer
  // (md-files/ui-development.md §3) are independent: collapsing the rail on desktop
  // shouldn't affect whether the drawer is open on a phone-width viewport, and vice
  // versa -- they're two different responses to two different space constraints.
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  // Tailwind's `md` breakpoint (768px), matching the `md:static md:translate-x-0`
  // classes below that promote the drawer to a permanently-visible desktop rail.
  const isDesktop = useMediaQuery('(min-width: 768px)')
  const auth = useAuth()
  // Hoisted here (rather than called again inside ProviderStatusPage) so the header
  // strip's compact bars and the full Providers tab share one `/api/llm/usage` poll
  // every 15s instead of each opening their own independent interval.
  const llmUsage = useLlmUsage()
  const meteredProviders = (llmUsage.data?.providers ?? []).filter((p) => p.budget !== null)
  const activeItem = NAV_ITEMS.find((item) => item.id === safeView)

  // §34 (file 12 prompt 2): every dashboard/chat/voice affordance below requires a
  // valid session -- reusing the same login flow protects the exact same backend
  // routes whether this build is being used locally (against a desktop deployment) or
  // over the public web, so it's simplest to just always gate on it rather than trying
  // to detect which deployment shape is currently in play.
  if (!auth.isAuthenticated) {
    return <LoginPage onLogin={auth.login} loggingIn={auth.loggingIn} error={auth.error} />
  }

  function selectView(next: View) {
    setView(next)
    setMobileNavOpen(false)
  }

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-text">
      {/* Mobile off-canvas backdrop -- tapping it closes the drawer, same as tapping a nav item. */}
      {mobileNavOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm md:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}

      <div
        className={cn(
          'fixed inset-y-0 left-0 z-50 transition-transform duration-300 md:static md:z-auto md:translate-x-0',
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full',
        )}
        // Off-screen (translate-x-full) on mobile when closed still leaves the nav
        // buttons inside focusable and in the tab order -- `inert` them in that state
        // so Tab doesn't jump into an invisible drawer. Never inert on desktop, where
        // `md:translate-x-0` always shows the rail regardless of `mobileNavOpen`.
        inert={!isDesktop && !mobileNavOpen}
      >
        <Sidebar
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((c) => !c)}
          header={
            <div className="flex items-center gap-2 overflow-hidden">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-primary/50 bg-primary/10 text-primary shadow-glow-primary">
                <Cpu className="h-4 w-4" />
              </div>
              {!collapsed && (
                <span className="truncate font-display text-sm font-semibold tracking-wide text-text">
                  JARVIS
                </span>
              )}
            </div>
          }
        >
          {NAV_ITEMS.map((item) => (
            <SidebarItem
              key={item.id}
              icon={item.icon}
              label={item.label}
              collapsed={collapsed}
              active={safeView === item.id}
              onClick={() => selectView(item.id)}
            />
          ))}
        </Sidebar>
      </div>

      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        {/* Page-header strip (md-files/ui-development.md §3): title + contextual
            actions, so users always know where they are beyond just the sidebar's
            active-item highlight. */}
        <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-border bg-surface/70 px-4 py-3 backdrop-blur-md md:px-6">
          <button
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open navigation"
            className="rounded-md p-1.5 text-text-muted transition-colors hover:text-primary md:hidden"
          >
            <Menu className="h-5 w-5" />
          </button>

          <h1 className="font-display text-lg font-semibold tracking-wide text-text">
            {activeItem?.label ?? ''}
          </h1>

          {/* Always-visible daily budget bars (§8/§39) -- e.g. Gemini's free-tier
              quota -- so a near-limit provider is visible from any tab, not just the
              Providers tab. Only metered providers render here; Ollama (local/
              unmetered) has nothing to bar-chart. Clicking jumps to the full
              Providers tab. */}
          {meteredProviders.length > 0 && (
            <button
              onClick={() => selectView('providers')}
              className="flex flex-1 flex-wrap items-center gap-x-6 gap-y-2"
              title="View AI provider status"
            >
              {meteredProviders.map((provider) => (
                <LimitBar
                  key={provider.provider}
                  compact
                  label={provider.provider}
                  used={provider.requests}
                  limit={provider.budget}
                  status={provider.status}
                />
              ))}
            </button>
          )}

          <button
            onClick={auth.logout}
            className="ml-auto flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm text-text-muted transition-colors hover:border-primary/50 hover:text-primary"
          >
            <LogOut className="h-4 w-4" />
            {auth.username ? `Log out (${auth.username})` : 'Log out'}
          </button>
        </header>

        <div className="flex-1">
          {safeView === 'chat' && <ChatPage />}
          {safeView === 'tasks' && <TasksPage />}
          {safeView === 'routines' && <RoutinesPage />}
          {safeView === 'providers' && <ProviderStatusPage llmUsage={llmUsage} />}
          {safeView === 'activity' && <ActivityPage />}
          {safeView === 'settings' && <SettingsPage />}
          {safeView === 'status' && <StatusPage />}
        </div>
      </div>

      <VoiceControl />
    </div>
  )
}

export default App
