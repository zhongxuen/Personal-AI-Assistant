import { useState } from 'react'
import { StatusPage } from './pages/StatusPage'
import { TasksPage } from './pages/Tasks'
import { RoutinesPage } from './pages/Routines'
import { ProviderStatusPage } from './pages/ProviderStatus'
import { SettingsPage } from './pages/Settings'
import { VoiceControl } from './components/VoiceControl'

type View = 'status' | 'tasks' | 'routines' | 'providers' | 'settings'

const TABS: { id: View; label: string }[] = [
  { id: 'tasks', label: 'Tasks' },
  { id: 'routines', label: 'Routines' },
  { id: 'providers', label: 'AI Providers' },
  { id: 'settings', label: 'Settings' },
  { id: 'status', label: 'Status' },
]

function App() {
  const [view, setView] = useState<View>('tasks')

  return (
    <div className="min-h-screen bg-slate-950">
      <nav className="flex justify-center gap-1 border-b border-slate-800 px-6 py-3">
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
      </nav>
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
