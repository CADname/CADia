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
  const navigate = useNavigate()

  const refreshUser = useCallback(async () => {
    try {
      setUser(await api<User>('/api/auth/me'))
    } catch {
      setUser(null)
    }
  }, [])

  useEffect(() => { void refreshUser() }, [refreshUser])

  async function logout() {
    await api('/api/auth/logout', { method: 'POST' })
    setUser(null)
    navigate('/login')
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
    </ErrorBoundary>
  )
}

export default App
