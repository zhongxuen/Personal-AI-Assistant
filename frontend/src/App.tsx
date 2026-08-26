import { useState } from 'react'
import { StatusPage } from './pages/StatusPage'
import { TasksPage } from './pages/Tasks'
import { RoutinesPage } from './pages/Routines'
import { ProviderStatusPage } from './pages/ProviderStatus'
import { SettingsPage } from './pages/Settings'
import { ChatPage } from './pages/Chat'
import { LoginPage } from './pages/Login'
import { VoiceControl } from './components/VoiceControl'
import { useAuth } from './hooks/useAuth'

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
      <nav className="flex items-center justify-center gap-1 border-b border-slate-800 px-6 py-3">
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
      </nav>
      {view === 'chat' && <ChatPage />}
      {view === 'tasks' && <TasksPage />}
      {view === 'routines' && <RoutinesPage />}
      {view === 'providers' && <ProviderStatusPage />}
      {view === 'settings' && <SettingsPage />}
      {view === 'status' && <StatusPage />}
      <VoiceControl />
    </div>
  )
}

export default App
