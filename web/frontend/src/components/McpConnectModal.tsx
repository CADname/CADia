import { useEffect, useMemo, useState } from 'react'
import { Check, Clipboard, Download, KeyRound, Network, Plus, Trash2, X } from 'lucide-react'
import { api, download } from '../api'

type TokenRow = {
  id: string
  label: string
  createdAt: string | null
  expiresAt: string | null
  lastUsedAt: string | null
  revokedAt: string | null
}

type CreatedToken = TokenRow & {
  token: string
  server: string
  rpcUrl: string
  bridgeDownload: string
  warning: string
}

type Props = { projectId: string; onClose: () => void }

export default function McpConnectModal({ projectId, onClose }: Props) {
  const [tokens, setTokens] = useState<TokenRow[]>([])
  const [created, setCreated] = useState<CreatedToken | null>(null)
  const [label, setLabel] = useState('My MCP connection')
  const [days, setDays] = useState(30)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState('')

  async function load() {
    try { setTokens(await api<TokenRow[]>(`/api/projects/${projectId}/mcp/tokens`)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  useEffect(() => { void load() }, [projectId])

  async function create() {
    setBusy(true); setError('')
    try {
      const value = await api<CreatedToken>(`/api/projects/${projectId}/mcp/tokens`, {
        method: 'POST', body: JSON.stringify({ label, expires_days: days }),
      })
      setCreated(value); await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }

  async function revoke(id: string) {
    try { await api<void>(`/api/projects/${projectId}/mcp/tokens/${id}`, { method: 'DELETE' }); await load() }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  async function copy(name: string, value: string) {
    try { await navigator.clipboard.writeText(value); setCopied(name); window.setTimeout(() => setCopied(''), 1200) }
    catch { setError('Could not copy to clipboard.') }
  }

  const command = useMemo(() => created ? `python cadia_web_mcp_bridge.py --server ${created.server} --project ${projectId}` : '', [created, projectId])
  const config = useMemo(() => created ? JSON.stringify({
    mcpServers: {
      cadia: {
        command: 'python',
        args: ['C:/PATH/cadia_web_mcp_bridge.py', '--server', created.server, '--project', projectId],
        env: { CADIA_MCP_TOKEN: created.token },
      },
    },
  }, null, 2) : '', [created, projectId])

  return <div className="modal-backdrop" onMouseDown={(e) => e.currentTarget === e.target && onClose()}>
    <div className="modal-card mcp-modal">
      <button className="modal-close" onClick={onClose}><X size={18} /></button>
      <div className="connect-logo"><Network size={24} /></div>
      <div className="eyebrow">UNIVERSAL MCP</div>
      <h2>Connect CAD to another AI</h2>
      <p className="muted">Create a project-scoped MCP token so external AI clients that support MCP can use the same CAD tools and core.</p>
      <div className="mcp-warning">The external AI client manages its own login and plan. Do not enter that AI password into CADia. Use HTTPS on the public internet.</div>

      {!created && <div className="mcp-create-row">
        <label>Connection name<input value={label} onChange={(e) => setLabel(e.target.value)} /></label>
        <label>Expires<select value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year</option></select></label>
        <button className="primary" disabled={busy} onClick={create}><Plus size={16} />{busy ? 'Creating…' : 'Create MCP token'}</button>
      </div>}

      {created && <div className="mcp-created">
        <div className="mcp-once"><KeyRound size={17} /><span><strong>The token is shown only once.</strong><small>{created.warning}</small></span></div>
        <div className="mcp-code-row"><code>{created.token}</code><button onClick={() => copy('token', created.token)}>{copied === 'token' ? <Check size={15}/> : <Clipboard size={15}/>}</button></div>
        <div className="mcp-step"><strong>1. Download bridge</strong><button onClick={() => download('/api/mcp/bridge.py')}><Download size={15}/> cadia_web_mcp_bridge.py</button></div>
        <div className="mcp-step"><strong>2. Direct run example</strong><div className="mcp-code-row"><code>{command}</code><button onClick={() => copy('cmd', command)}>{copied === 'cmd' ? <Check size={15}/> : <Clipboard size={15}/>}</button></div></div>
        <div className="mcp-step"><strong>3. stdio MCP config example</strong><div className="mcp-config"><pre>{config}</pre><button onClick={() => copy('config', config)}>{copied === 'config' ? <Check size={15}/> : <Clipboard size={15}/>}</button></div></div>
        <button className="secondary wide" onClick={() => setCreated(null)}>Create another token</button>
      </div>}

      <div className="mcp-token-list">
        <strong>Issued connections</strong>
        {tokens.length === 0 ? <span className="muted">No MCP tokens issued yet.</span> : tokens.map((row) => <div className={`mcp-token-item ${row.revokedAt ? 'revoked' : ''}`} key={row.id}>
          <span><b>{row.label}</b><small>{row.revokedAt ? 'Revoked' : `Expires ${row.expiresAt ? new Date(row.expiresAt).toLocaleDateString() : '-'}`}</small></span>
          {!row.revokedAt && <button title="Revoke token" onClick={() => revoke(row.id)}><Trash2 size={15}/></button>}
        </div>)}
      </div>
      {error && <div className="inline-error">{error}</div>}
    </div>
  </div>
}
