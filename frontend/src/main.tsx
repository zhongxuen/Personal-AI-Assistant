import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import './index.css'
import App from './App.tsx'
import { ToastProvider } from './components/ui'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* `reducedMotion="user"` (md-files/ui-development.md §7 milestone 6): the plain
        CSS `prefers-reduced-motion` rule in index.css only neuters CSS
        animations/transitions, but Stagger/Modal/Toast/Chat/Tasks/Login drive their
        entrance motion through framer-motion's JS-driven transforms, which don't go
        through CSS `transition`/`animation` at all. This makes every `motion.*` element
        in the tree check the OS setting itself and collapse transforms/layout
        animation to instant. */}
    <MotionConfig reducedMotion="user">
      {/* Mounted once at the root (md-files/ui-development.md §4) so any page can call
          useToast() -- Settings.tsx's inline save confirmations are the first consumer. */}
      <ToastProvider>
        <App />
      </ToastProvider>
    </MotionConfig>
  </StrictMode>,
)
