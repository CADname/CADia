import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api } from './api'
import AuthScreen from './components/AuthScreen'
import CadWorkspace from './components/CadWorkspace'
import ErrorBoundary from './components/ErrorBoundary'
import ProjectDashboard from './components/ProjectDashboard'
import type { User } from './types'

function BootScreen() {
  return <div className="boot-screen"><div className="brand-mark large">C</div><div className="boot-line" /></div>
}

function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [guestLogoutOpen, setGuestLogoutOpen] = useState(false)
  const [loggingOut, setLoggingOut] = useState(false)
  const navigate = useNavigate()

  const refreshUser = useCallback(async () => {
    try {
      setUser(await api<User>('/api/auth/me'))
    } catch {
      setUser(null)
    }
  }, [])

  useEffect(() => { void refreshUser() }, [refreshUser])

  async function performLogout() {
    setLoggingOut(true)
    try {
      await api('/api/auth/logout', { method: 'POST' })
      setGuestLogoutOpen(false)
      setUser(null)
      navigate('/login')
    } finally {
      setLoggingOut(false)
    }
  }

  function logout() {
    const isGuest = user?.email.toLowerCase().startsWith('guest-') && user.email.toLowerCase().endsWith('@cadia.local')
    if (isGuest) {
      setGuestLogoutOpen(true)
      return
    }
    void performLogout()
  }

  if (user === undefined) return <BootScreen />

  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <AuthScreen onAuthenticated={setUser} />} />
        <Route path="/" element={user ? <ProjectDashboard user={user} onLogout={logout} /> : <Navigate to="/login" replace />} />
        <Route path="/projects/:projectId" element={user ? <CadWorkspace user={user} onLogout={logout} /> : <Navigate to="/login" replace />} />
        <Route path="*" element={<Navigate to={user ? '/' : '/login'} replace />} />
      </Routes>
      {guestLogoutOpen && (
        <div className="modal-backdrop" onMouseDown={() => { if (!loggingOut) setGuestLogoutOpen(false) }}>
          <div className="modal-card" onMouseDown={(event) => event.stopPropagation()}>
            <h2>Sign out of this guest session?</h2>
            <p className="muted">Signing out will permanently delete all projects, CAD models, chat history, uploaded and exported files, and connected AI data created in this guest session. This cannot be undone.</p>
            <div className="modal-actions">
              <button className="secondary" onClick={() => setGuestLogoutOpen(false)} disabled={loggingOut}>Cancel</button>
              <button className="secondary danger" onClick={() => void performLogout()} disabled={loggingOut}>{loggingOut ? 'Deleting…' : 'Sign out & delete data'}</button>
            </div>
          </div>
        </div>
      )}
    </ErrorBoundary>
  )
}

export default App
