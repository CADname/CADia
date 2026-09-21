import { useEffect, useMemo, useState } from 'react'
import { Check, Clipboard, ExternalLink, KeyRound, LogOut, ShieldCheck, Sparkles, X } from 'lucide-react'
import { api } from '../api'
import type { AccountInfo } from '../types'

type DeviceLogin = { type: string; loginId: string; verificationUrl: string; userCode: string }
type LoginStatus = { pending: boolean; success: boolean | null; error?: string | null }
type ProviderId = 'codex' | 'copilot' | 'openai' | 'anthropic' | 'gemini'
type ProviderRow = { id: ProviderId; label: string; connected?: boolean; configured?: boolean; selected?: boolean; model?: string | null }
type ProviderList = { selected: ProviderId; autoFallback?: boolean; providers: ProviderRow[] }
type Props = { account: AccountInfo | null; onAccount: (account: AccountInfo | null) => void; onClose: () => void }

const providerInfo: Record<ProviderId, { label: string; caption: string; keyLabel?: string; placeholder?: string }> = {
  codex: { label: 'ChatGPT / Codex', caption: 'Use your ChatGPT account and limits' },
  copilot: { label: 'GitHub Copilot', caption: 'Use your GitHub Copilot subscription · no API key required' },
  openai: { label: 'OpenAI API', caption: 'Use your OpenAI API key', keyLabel: 'OpenAI API key', placeholder: 'sk-...' },
  anthropic: { label: 'Claude', caption: 'Use your Anthropic API key', keyLabel: 'Anthropic API key', placeholder: 'sk-ant-...' },
  gemini: { label: 'Gemini', caption: 'Use your Gemini API key', keyLabel: 'Gemini API key', placeholder: 'AIza...' },
}

export default function AIConnectModal({ account, onAccount, onClose }: Props) {
  const initial = (account?.provider as ProviderId) || 'codex'
  const [provider, setProvider] = useState<ProviderId>(initial)
  const [providerList, setProviderList] = useState<ProviderList | null>(null)
  const [device, setDevice] = useState<DeviceLogin | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState(account?.model || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const row = useMemo(() => providerList?.providers.find((x) => x.id === provider), [providerList, provider])
  const connectedHere = Boolean(row?.connected || row?.configured)
  const selectedHere = providerList?.selected === provider || account?.provider === provider
  const info = providerInfo[provider]
  const isApi = !['codex', 'copilot'].includes(provider)

  async function refreshProviders() {
    const list = await api<ProviderList>('/api/ai/providers')
    setProviderList(list)
    return list
  }

  async function refreshAccount() {
    const next = await api<AccountInfo>('/api/ai/account')
    onAccount(next)
    await refreshProviders()
    return next
  }

  useEffect(() => { void refreshProviders().catch(() => {}) }, [])

  useEffect(() => {
    if (!device || connectedHere) return
    const timer = window.setInterval(async () => {
      try {
        const status = await api<LoginStatus>(`/api/ai/login/${device.loginId}/status`)
        if (!status.pending && status.success === false) {
          setError(status.error || 'ChatGPT sign-in was not completed.')
          setDevice(null)
          return
        }
        const next = await refreshAccount()
        if (next.connected) setDevice(null)
      } catch { /* keep polling */ }
    }, 2200)
    return () => window.clearInterval(timer)
  }, [device, connectedHere])

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || event.data?.type !== 'cadia:copilot-oauth') return
      if (event.data?.ok) void refreshAccount().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      else setError('GitHub Copilot connection was not completed.')
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [])

  useEffect(() => {
    setError(''); setDevice(null); setApiKey('')
    setModel(row?.model || (account?.provider === provider ? account?.model || '' : ''))
  }, [provider, row?.model])

  async function selectExisting() {
    setBusy(true); setError('')
    try {
      await api<AccountInfo>('/api/ai/provider/select', { method: 'POST', body: JSON.stringify({ provider }) })
      await refreshAccount()
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function connectCodex() {
    if (connectedHere) return void selectExisting()
    const authWindow = window.open('about:blank', '_blank')
    if (authWindow) authWindow.opener = null
    setBusy(true); setError('')
    try {
      const selected = await api<AccountInfo>('/api/ai/provider/connect', { method: 'POST', body: JSON.stringify({ provider: 'codex' }) })
      if (selected.connected) {
        if (authWindow) authWindow.close()
        onAccount(selected); await refreshProviders(); return
      }
      const login = await api<DeviceLogin>('/api/ai/login/device', { method: 'POST' })
      setDevice(login)
      if (authWindow) authWindow.location.replace(login.verificationUrl)
      else setError('The popup was blocked. Open the authorization page link manually below.')
    } catch (reason) {
      if (authWindow) authWindow.close()
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally { setBusy(false) }
  }

  async function connectCopilot() {
    if (connectedHere) return void selectExisting()
    setBusy(true); setError('')
    try {
      const login = await api<{ authorizationUrl: string }>('/api/ai/copilot/login')
      const popup = window.open(login.authorizationUrl, 'cadia-github-copilot', 'popup=yes,width=720,height=760')
      if (!popup) { setError('The popup was blocked. Please allow popups in your browser.'); return }
      const timer = window.setInterval(async () => {
        if (popup.closed) {
          window.clearInterval(timer)
          try { await refreshAccount() } catch { /* handled by postMessage/poll */ }
        } else {
          try {
            const list = await refreshProviders()
            const connected = list.providers.some((x) => x.id === 'copilot' && (x.connected || x.configured))
            if (connected) { window.clearInterval(timer); try { popup.close() } catch {} ; await refreshAccount() }
          } catch { /* keep polling */ }
        }
      }, 1800)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function connectApi() {
    if (connectedHere && !apiKey.trim()) return void selectExisting()
    setBusy(true); setError('')
    try {
      const next = await api<AccountInfo>('/api/ai/provider/connect', {
        method: 'POST', body: JSON.stringify({ provider, api_key: apiKey, model: model || null }),
      })
      onAccount(next); setApiKey(''); await refreshProviders()
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function disconnect() {
    setBusy(true); setError('')
    try {
      const next = await api<AccountInfo>('/api/ai/provider/disconnect', { method: 'POST' })
      onAccount(next); await refreshProviders()
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function copyCode() {
    if (!device) return
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(device.userCode)
      else { const textarea=document.createElement('textarea'); textarea.value=device.userCode; textarea.style.position='fixed'; textarea.style.opacity='0'; document.body.appendChild(textarea); textarea.focus(); textarea.select(); document.execCommand('copy'); textarea.remove() }
      setCopied(true); window.setTimeout(() => setCopied(false), 1600)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not copy the code.') }
  }

  async function closeModal() {
    if (device) { try { await api(`/api/ai/login/${device.loginId}/cancel`, { method: 'POST' }) } catch {} }
    onClose()
  }

  return <div className="modal-backdrop" onMouseDown={() => void closeModal()}>
    <div className="modal-card codex-modal ai-provider-modal" onMouseDown={(event) => event.stopPropagation()}>
      <button className="modal-close" onClick={() => void closeModal()}><X size={18}/></button>
      <span className="connect-logo"><Sparkles/></span><p className="eyebrow">AI CONNECTION</p><h2>Choose an AI provider</h2>
      <p className="muted">You can connect multiple AI providers, but each modeling request uses the selected provider. If Codex and Copilot are both connected, CADia can fall back once when the selected provider fails.</p>
      <div className="provider-grid">{(Object.keys(providerInfo) as ProviderId[]).map((id) => {
        const status=providerList?.providers.find((x)=>x.id===id); const connected=Boolean(status?.connected||status?.configured); const selected=providerList?.selected===id
        return <button key={id} className={`provider-card ${provider===id?'active':''}`} onClick={()=>setProvider(id)}><strong>{providerInfo[id].label}{selected?' · Active':''}</strong><small>{providerInfo[id].caption}{connected?' · Connected':''}</small></button>
      })}</div>
      <div className="provider-detail">
        <div className="provider-title"><div><strong>{info.label}</strong><small>{info.caption}</small></div>{connectedHere&&<span className="provider-connected"><Check size={13}/> {selectedHere?'Active':'Connected'}</span>}</div>
        {connectedHere ? <>
          <div className="connected-account"><span><Check size={18}/></span><div><strong>{selectedHere ? (account?.account?.email || info.label) : info.label}</strong><small>{row?.model || (selectedHere ? account?.model : '') || 'Connected'}</small></div></div>
          <p className="security-copy"><ShieldCheck size={16}/>{provider==='codex'?" ChatGPT authentication is managed inside this user's isolated Codex environment.":provider==='copilot'?" GitHub OAuth tokens are encrypted on the server and requests use the user's Copilot subscription.":' API keys are encrypted on the server and are not stored in the browser.'}</p>
          <div className="modal-actions">{selectedHere&&<button className="secondary danger" onClick={disconnect} disabled={busy}><LogOut size={16}/> Disconnect</button>}{!selectedHere&&<button className="primary" onClick={selectExisting} disabled={busy}>Use this AI</button>}<button className="primary" onClick={onClose}>Done</button></div>
        </> : provider==='codex' ? device ? <>
          <p className="muted">Enter this code on the OpenAI authorization page that just opened.</p><button className="device-code" onClick={copyCode}><strong>{device.userCode}</strong>{copied?<Check size={17}/>:<Clipboard size={17}/>}</button><a className="verification-link" href={device.verificationUrl} target="_blank" rel="noreferrer">Open authorization page again <ExternalLink size={15}/></a><div className="waiting-line"><span className="spinner"/> Waiting for sign-in to complete…</div>
        </> : <>{error&&<div className="form-error">{error}</div>}<button className="primary wide" onClick={connectCodex} disabled={busy}>{busy?'Preparing connection…':'Connect ChatGPT / Codex'} <ExternalLink size={16}/></button></>
        : provider==='copilot' ? <><div className="connect-steps"><span>1</span><p><strong>GitHub authorization</strong><small>Authorize CADia with your own GitHub account.</small></p><span>2</span><p><strong>Use your Copilot subscription</strong><small>Requests use your own Copilot access and usage limits.</small></p><span>3</span><p><strong>CADia shared planner</strong><small>Uses the same CAD planning, verification, and recovery pipeline.</small></p></div>{error&&<div className="form-error">{error}</div>}<button className="primary wide" onClick={connectCopilot} disabled={busy}>{busy?'Preparing connection…':'GitHub Copilot Connect'} <ExternalLink size={16}/></button></>
        : <><label>{info.keyLabel}<input type="password" autoComplete="off" value={apiKey} onChange={(e)=>setApiKey(e.target.value)} placeholder={info.placeholder}/></label><label>Default model ID <input value={model} onChange={(e)=>setModel(e.target.value)} placeholder="Leave empty to auto-select an available model"/></label><div className="api-key-note"><KeyRound size={15}/><span>The key is verified and encrypted on the server, and is never shown again. API usage is billed separately by that provider.</span></div>{error&&<div className="form-error">{error}</div>}<button className="primary wide" onClick={connectApi} disabled={busy||(!apiKey.trim()&&!connectedHere)}>{busy?'Checking API…':`${info.label} Connect`}</button></>}
      </div>
    </div>
  </div>
}
