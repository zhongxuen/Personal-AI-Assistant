import { useState } from 'react'
import { StatusPage } from './pages/StatusPage'
import { TasksPage } from './pages/Tasks'
import { RoutinesPage } from './pages/Routines'

type View = 'status' | 'tasks' | 'routines'

const TABS: { id: View; label: string }[] = [
  { id: 'tasks', label: 'Tasks' },
  { id: 'routines', label: 'Routines' },
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
      {view === 'status' && <StatusPage />}
    </div>
  )
}

export default App
