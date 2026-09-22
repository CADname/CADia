import { FormEvent, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Box, Check, Cpu, LockKeyhole, Orbit, PlayCircle } from 'lucide-react'
import { api } from '../api'
import type { Project, User } from '../types'

type Props = { onAuthenticated: (user: User) => void }
type GuestLaunch = { user: User; project: Project }

export default function AuthScreen({ onAuthenticated }: Props) {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState(() => {
    const oauthError = new URLSearchParams(window.location.search).get('oauth_error')
    return oauthError ? `Google sign-in was not completed: ${oauthError}` : ''
  })
  const [busy, setBusy] = useState(false)
  const [guestBusy, setGuestBusy] = useState(false)

  const googleLogin = () => { window.location.href = '/api/auth/google/login' }

  async function launchGuest() {
    setError('')
    setGuestBusy(true)
    try {
      const payload = await api<GuestLaunch>('/api/auth/guest', { method: 'POST' })
      onAuthenticated(payload.user)
      navigate(`/projects/${payload.project.id}`, { replace: true })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setGuestBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const user = await api<User>(`/api/auth/${mode === 'login' ? 'login' : 'register'}`, {
        method: 'POST',
        body: JSON.stringify(mode === 'login' ? { email, password } : { email, password, display_name: displayName }),
      })
      onAuthenticated(user)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page global-auth">
      <section className="auth-visual">
        <div className="auth-orbit orbit-one" />
        <div className="auth-orbit orbit-two" />
        <div className="auth-copy">
          <div className="brand-lockup"><span className="brand-mark">C</span><span>CADia</span></div>
          <p className="eyebrow">AI-NATIVE PARAMETRIC CAD</p>
          <h1>Describe it.<br /><span>Engineer it.</span></h1>
          <p className="auth-lead">AI-native parametric CAD that turns engineering intent into editable B-Rep models, feature history, and assemblies.</p>
          <div className="feature-pills">
            <span><Cpu size={15} /> CadQuery / OCCT</span>
            <span><Orbit size={15} /> Face & Edge editing</span>
            <span><LockKeyhole size={15} /> Per-user Codex isolation</span>
          </div>
        </div>
        <div className="wire-object" aria-hidden="true">
          <Box size={220} strokeWidth={0.45} />
          <div className="wire-disc disc-a" />
          <div className="wire-disc disc-b" />
        </div>
      </section>
      <section className="auth-form-side">
        <form className="auth-card" onSubmit={submit}>
          <div className="mobile-brand"><span className="brand-mark">C</span> CADia</div>
          <p className="eyebrow">INSTANT ACCESS</p>
          <h2>Launch the CAD workspace</h2>
          <p className="muted">Start instantly without creating an account. Connect ChatGPT/Codex when you want to run live AI modeling.</p>
          <button type="button" className="primary wide launch-demo-button" onClick={launchGuest} disabled={guestBusy || busy}>
            {guestBusy ? 'Launching…' : 'Launch CADia'} <PlayCircle size={17} />
          </button>
          <div className="auth-note"><Check size={14} /> Guest sessions are isolated. Your guest workspace and its data are permanently deleted when you sign out.</div>

          <div className="auth-divider"><span>Returning user</span></div>
          <p className="eyebrow">{mode === 'login' ? 'SIGN IN' : 'CREATE WORKSPACE'}</p>
          <h2>{mode === 'login' ? 'Continue designing' : 'Create your account'}</h2>
          <p className="muted">After CADia sign-in, you can connect ChatGPT/Codex, GitHub Copilot, Claude, Gemini, or an OpenAI API key.</p>
          <button type="button" className="google-login-button" onClick={googleLogin} disabled={busy || guestBusy}>
            <span className="google-g">G</span><span>Continue with Google</span>
          </button>
          <div className="auth-divider"><span>or</span></div>
          {mode === 'register' && (
            <label>Name<input autoComplete="name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Your name" required /></label>
          )}
          <label>Email<input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" required /></label>
          <label>Password<input type="password" minLength={8} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" required /></label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary wide" disabled={busy || guestBusy}>
            {busy ? 'Working…' : mode === 'login' ? 'Sign in' : 'Create account'} <ArrowRight size={17} />
          </button>
          <button type="button" className="text-button" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}>
            {mode === 'login' ? 'New to CADia? Create an account' : 'Already have an account? Sign in'}
          </button>
          <div className="auth-note"><Check size={14} /> Provider credentials are isolated by provider and encrypted on the server.</div>
        </form>
      </section>
    </main>
  )
}
