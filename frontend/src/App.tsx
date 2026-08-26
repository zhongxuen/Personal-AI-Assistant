import { useState } from 'react'
import { StatusPage } from './pages/StatusPage'
import { TasksPage } from './pages/Tasks'
import { RoutinesPage } from './pages/Routines'
import { ProviderStatusPage } from './pages/ProviderStatus'
import { SettingsPage } from './pages/Settings'
import { ChatPage } from './pages/Chat'
import { LoginPage } from './pages/Login'
import { VoiceControl } from './components/VoiceControl'
import { LimitBar } from './components/LimitBar'
import { useAuth } from './hooks/useAuth'
import { useLlmUsage } from './hooks/useLlmUsage'

type View = 'chat' | 'status' | 'tasks' | 'routines' | 'providers' | 'settings'

const TABS: { id: View; label: string }[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'routines', label: 'Routines' },
  { id: 'providers', label: 'AI Providers' },
  { id: 'settings', label: 'Settings' },
  { id: 'status', label: 'Status' },
]

function App() {
  const [view, setView] = useState<View>('chat')
  const auth = useAuth()
  // Hoisted here (rather than called again inside ProviderStatusPage) so the nav
  // strip's compact bars and the full Providers tab share one `/api/llm/usage`
  // poll every 15s instead of each opening their own independent interval.
  const llmUsage = useLlmUsage()
  const meteredProviders = (llmUsage.data?.providers ?? []).filter((p) => p.budget !== null)

  // §34 (file 12 prompt 2): every dashboard/chat/voice affordance below requires a
  // valid session -- reusing the same login flow protects the exact same backend
  // routes whether this build is being used locally (against a desktop deployment) or
  // over the public web, so it's simplest to just always gate on it rather than trying
  // to detect which deployment shape is currently in play.
  if (!auth.isAuthenticated) {
    return <LoginPage onLogin={auth.login} loggingIn={auth.loggingIn} error={auth.error} />
  }

  return (
    <div className="min-h-screen bg-slate-950">
      <nav className="border-b border-slate-800 px-6 py-3">
        <div className="flex items-center justify-center gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setView(tab.id)}
              className={`rounded px-3 py-1.5 text-sm font-medium transition ${
                view === tab.id ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {tab.label}
            </button>
          ))}
          <button
            onClick={auth.logout}
            className="ml-4 rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            {auth.username ? `Log out (${auth.username})` : 'Log out'}
          </button>
        </div>
        {/* Always-visible daily budget bars (§8/§39) -- e.g. Gemini's free-tier quota --
            so a near-limit provider is visible from any tab, not just the Providers
            tab. Only metered providers render here; Ollama (local/unmetered) has
            nothing to bar-chart. Clicking jumps to the full Providers tab. */}
        {meteredProviders.length > 0 && (
          <button
            onClick={() => setView('providers')}
            className="mt-3 flex flex-wrap items-center justify-center gap-x-6 gap-y-2"
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
      </nav>
      {view === 'chat' && <ChatPage />}
      {view === 'tasks' && <TasksPage />}
      {view === 'routines' && <RoutinesPage />}
      {view === 'providers' && <ProviderStatusPage llmUsage={llmUsage} />}
      {view === 'settings' && <SettingsPage />}
      {view === 'status' && <StatusPage />}
      <VoiceControl />
    </div>
  )
}

export default App
