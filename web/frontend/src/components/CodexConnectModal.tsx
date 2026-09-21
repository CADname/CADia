import { useEffect, useState } from 'react'
import { Check, Clipboard, ExternalLink, Link2, LogOut, ShieldCheck, X } from 'lucide-react'
import { api } from '../api'
import type { AccountInfo } from '../types'

type DeviceLogin = { type: string; loginId: string; verificationUrl: string; userCode: string }
type LoginStatus = { pending: boolean; success: boolean | null; error?: string | null }
type Props = { account: AccountInfo | null; onAccount: (account: AccountInfo | null) => void; onClose: () => void }

export default function CodexConnectModal({ account, onAccount, onClose }: Props) {
  const [device, setDevice] = useState<DeviceLogin | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!device || account?.account) return
    const timer = window.setInterval(async () => {
      try {
        const status = await api<LoginStatus>(`/api/ai/login/${device.loginId}/status`)
        if (!status.pending && status.success === false) {
          setError(status.error || 'ChatGPT sign-in was not completed.')
          setDevice(null)
          return
        }
        const next = await api<AccountInfo>('/api/ai/account')
        onAccount(next)
        if (next.account) setDevice(null)
      } catch { /* keep polling while the device ceremony is open */ }
    }, 2200)
    return () => window.clearInterval(timer)
  }, [device, account?.account, onAccount])

  async function connect() {
    // Open synchronously so Edge/Chrome do not treat the auth page as an
    // async popup and block it after the API request completes.
    const authWindow = window.open('about:blank', '_blank')
    if (authWindow) authWindow.opener = null
    setBusy(true); setError('')
    try {
      const login = await api<DeviceLogin>('/api/ai/login/device', { method: 'POST' })
      setDevice(login)
      if (authWindow) authWindow.location.replace(login.verificationUrl)
      else setError('The popup was blocked. Open the authorization page link manually below.')
    } catch (reason) {
      if (authWindow) authWindow.close()
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(false) }
  }

  async function disconnect() {
    setBusy(true); setError('')
    try {
      await api('/api/ai/logout', { method: 'POST' })
      onAccount({ account: null, requiresOpenaiAuth: true })
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function copyCode() {
    if (!device) return
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(device.userCode)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = device.userCode
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        document.execCommand('copy')
        textarea.remove()
      }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not copy the code.')
    }
  }

  async function closeModal() {
    if (device) {
      try { await api(`/api/ai/login/${device.loginId}/cancel`, { method: 'POST' }) } catch { /* best effort */ }
      setDevice(null)
    }
    onClose()
  }

  return (
    <div className="modal-backdrop" onMouseDown={() => void closeModal()}>
      <div className="modal-card codex-modal" onMouseDown={(event) => event.stopPropagation()}>
        <button className="modal-close" onClick={() => void closeModal()}><X size={18} /></button>
        <span className="connect-logo"><Link2 /></span>
        <p className="eyebrow">CODEX CONNECTION</p>
        <h2>{account?.account ? 'Codex is connected' : 'Connect your ChatGPT account'}</h2>
        {account?.account ? <>
          <div className="connected-account"><span><Check size={18} /></span><div><strong>{account.account.email || 'ChatGPT account'}</strong><small>{account.account.planType || account.account.type} plan · per-user isolated session</small></div></div>
          <p className="security-copy"><ShieldCheck size={16} /> Authentication tokens are managed by Codex inside this user's isolated Codex environment.</p>
          <div className="modal-actions"><button className="secondary danger" onClick={disconnect} disabled={busy}><LogOut size={16} /> Disconnect</button><button className="primary" onClick={onClose}>Done</button></div>
        </> : device ? <>
          <p className="muted">Enter this code on the OpenAI authorization page that just opened.</p>
          <button className="device-code" onClick={copyCode}><strong>{device.userCode}</strong>{copied ? <Check size={17} /> : <Clipboard size={17} />}</button>
          <a className="verification-link" href={device.verificationUrl} target="_blank" rel="noreferrer">Open authorization page again <ExternalLink size={15} /></a>
          <div className="waiting-line"><span className="spinner" /> Waiting for sign-in to complete…</div>
          <button className="secondary wide" onClick={() => void closeModal()}>Cancel sign-in</button>
        </> : <>
          <p className="muted">Use your own ChatGPT/Codex access and usage limits without entering an API key.</p>
          <div className="connect-steps"><span>1</span><p><strong>Get a device code</strong><small>CADia requests a one-time code.</small></p><span>2</span><p><strong>Sign in directly with OpenAI</strong><small>Your password is never sent to CADia.</small></p><span>3</span><p><strong>Use your models and limits</strong><small>Each account gets isolated processes and storage.</small></p></div>
          {error && <div className="form-error">{error}</div>}
          <button className="primary wide" onClick={connect} disabled={busy}>{busy ? 'Preparing connection…' : 'Connect ChatGPT'} <ExternalLink size={16} /></button>
        </>}
      </div>
    </div>
  )
}
