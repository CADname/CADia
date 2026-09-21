import { ChangeEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  Box, Camera, ChevronDown, Cloud, Download, Factory, FolderOpen, Grid3X3,
  Layers3, LogOut, Maximize2, Moon, MousePointer2, Redo2, Save, ScanLine, Scissors,
  SquareMousePointer, Sun, Undo2, Upload, View, Waypoints, Workflow, X,
} from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, download, websocketUrl } from '../api'
import type { AccountInfo, CadMesh, CadState, ChatMessage, ModelInfo, Project, Selection, User } from '../types'
import CadViewer from './CadViewer'
import ChatPanel from './ChatPanel'
import AIConnectModal from './AIConnectModal'
import FeatureTree from './FeatureTree'
import ManufacturingPanel from './ManufacturingPanel'

type Props = { user: User; onLogout: () => void }
type Progress = { message: string; percent: number }


type VisibleMessageKind = 'progress' | 'result' | 'error'

function translateCoreMessage(raw: unknown, kind: VisibleMessageKind = 'progress'): string {
  let text = String(raw ?? '').trim()
  if (!text) return kind === 'progress' ? 'Working…' : kind === 'result' ? 'Operation completed.' : 'Unknown error'

  const exact: Record<string, string> = {
    '\uC694\uCCAD \uD574\uC11D \uC911…': 'Interpreting request…',
    '\uBAA8\uB378\uB9C1 \uACC4\uD68D \uD655\uC778 \uC911…': 'Reviewing modeling plan…',
    '\uD45C\uC900 \uD615\uC0C1 \uACC4\uD68D \uC644\uB8CC': 'Standard geometry plan completed',
    '\uBAA8\uB378 \uACB0\uACFC \uAC80\uC99D \uC911…': 'Validating model result…',
    '\uC548\uC804\uD55C \uC790\uB3D9 \uBCF5\uAD6C \uACBD\uB85C \uC7AC\uC2E4\uD589 \uC911…': 'Retrying safe automatic recovery…',
    '\uBB38\uC11C\uB97C \uB2EB\uC558\uC2B5\uB2C8\uB2E4. \uC5F4\uB9B0 CAD \uBB38\uC11C\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.': 'Document closed. No CAD document is open.',
    'AI \uC2E4\uD589\uAE30\uB97C \uCC3E\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4. \uC571\uC5D0 \uB85C\uADF8\uC778\uD55C \uB4A4 \uB2E4\uC2DC \uC2E4\uD589\uD574 \uC8FC\uC138\uC694.': 'AI runner was not found. Sign in and try again.',
  }
  if (exact[text]) return exact[text]

  text = text
    .replace(/^\uBAA8\uB378\uB9C1 \uC911…\s*(\d+\/\d+)$/, 'Modeling… $1')
    .replace(/^\uBAA8\uB378\uB9C1 \uB2E8\uACC4 \uC644\uB8CC\s*·\s*(\d+\/\d+)$/, 'Modeling step complete · $1')
    .replace(/^\uBAA8\uB378\uB9C1 \uC911\uB2E8 \uAC10\uC9C0\s*·\s*(.+?)\s+\uBCF5\uAD6C \uD655\uC778 \uC911…$/, 'Modeling interruption detected · checking $1 recovery…')
    .replace(/^\uBCF5\uAD6C \uACC4\uD68D\s+(\d+\/\d+)\s+\uACC4\uC0B0 \uC911\s*·\s*(.+)$/, 'Preparing recovery plan $1 · $2')
    .replace(/\uD53C\uCE58\uC6D0/g, 'Pitch diameter')
    .replace(/\uBC14\uAE65\uC9C0\uB984/g, 'Outside diameter')
    .replace(/\uB8E8\uD2B8\uC9C0\uB984/g, 'Root diameter')
    .replace(/\uD53C\uCC98/g, 'Feature')
    .replace(/\s+\uBAA8\uB378\uB9C1 \uC644\uB8CC\s*·\s*/g, ' modeling complete · ')

  // The English deployment never exposes an untranslated Korean core status.
  // Keep the original CAD core untouched; only sanitize its presentation at the web boundary.
  if (/[\uAC00-\uD7A3]/.test(text)) {
    if (kind === 'progress') return 'Processing CAD operation…'
    if (kind === 'result') return 'CAD operation completed.'
    return 'CAD operation failed. Check the server logs for details.'
  }
  return text
}

const orientations = [
  ['front', 'F'], ['back', 'B'], ['top', 'T'], ['bottom', 'D'], ['left', 'L'], ['right', 'R'], ['iso_top_right', 'ISO'],
] as const

export default function CadWorkspace({ user, onLogout }: Props) {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState<Project | null>(null)
  const [state, setState] = useState<CadState | null>(null)
  const [mesh, setMesh] = useState<CadMesh | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState('')
  const [account, setAccount] = useState<AccountInfo | null>(null)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [selectedModel, setSelectedModel] = useState('')
  const [effort, setEffort] = useState('medium')
  const [showConnect, setShowConnect] = useState(false)
  const [showManufacturing, setShowManufacturing] = useState(false)
  const [selectionMode, setSelectionMode] = useState<'face' | 'edge' | 'object'>('face')
  const [showEdges, setShowEdges] = useState(true)
  const [showGrid, setShowGrid] = useState(true)
  const [transparent, setTransparent] = useState(false)
  const [sectionAxis, setSectionAxis] = useState<'off' | 'x' | 'y' | 'z'>('off')
  const [sectionOffset, setSectionOffset] = useState(0.5)
  const [orientation, setOrientation] = useState('iso_top_right')
  const [viewRevision, setViewRevision] = useState(0)
  const [treeTab, setTreeTab] = useState<'model' | 'parameters'>('model')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => ((localStorage.getItem('cadia-theme') ?? localStorage.getItem('standalonecad-theme')) === 'light' ? 'light' : 'dark'))
  const [connection, setConnection] = useState<'connecting' | 'online' | 'offline'>('connecting')
  const [usage, setUsage] = useState<number | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const uploadRef = useRef<HTMLInputElement>(null)

  const showError = useCallback((reason: unknown) => {
    const message = reason instanceof Error ? reason.message : String(reason)
    setError(message)
    window.setTimeout(() => setError((current) => current === message ? '' : current), 5000)
  }, [])

  const loadMesh = useCallback(async () => setMesh(await api<CadMesh>(`/api/projects/${projectId}/cad/mesh?edges=true`)), [projectId])

  const loadAccount = useCallback(async () => {
    try {
      const next = await api<AccountInfo>('/api/ai/account')
      setAccount(next)
      if (next.connected || next.account) {
        const modelPayload = await api<{ models: ModelInfo[] }>('/api/ai/models')
        setModels(modelPayload.models)
        const preferred = modelPayload.models.find((item) => item.isDefault) || modelPayload.models[0]
        if (preferred) setSelectedModel(preferred.model || preferred.id)
        if (next.provider === 'codex') api<any>('/api/ai/rate-limits').then((limits) => setUsage(limits.rateLimits?.primary?.usedPercent ?? null)).catch(() => {})
        else setUsage(null)
      } else {
        setModels([]); setSelectedModel(''); setUsage(null)
      }
    } catch {
      setAccount({ provider: 'codex', providerLabel: 'ChatGPT / Codex', connected: false, account: null, requiresOpenaiAuth: true })
    }
  }, [])

  useEffect(() => {
    Promise.all([
      api<Project>(`/api/projects/${projectId}`),
      api<CadState>(`/api/projects/${projectId}/cad/state`),
      api<CadMesh>(`/api/projects/${projectId}/cad/mesh?edges=true`),
      api<ChatMessage[]>(`/api/projects/${projectId}/messages`),
    ]).then(([projectValue, stateValue, meshValue, history]) => {
      setProject(projectValue); setState(stateValue); setMesh(meshValue); setMessages(history)
      setOrientation(stateValue.document?.view_orientation || 'iso_top_right')
    }).catch(showError)
    void loadAccount()
  }, [projectId, loadAccount, showError])

  useEffect(() => {
    // External MCP clients mutate the same project outside this browser WebSocket.
    // Poll only lightweight state; fetch mesh again only when the CAD revision changes.
    let stopped = false
    const timer = window.setInterval(async () => {
      if (stopped || busy) return
      try {
        const next = await api<CadState>(`/api/projects/${projectId}/cad/state`)
        const currentRevision = state?.document?.revision ?? -1
        const nextRevision = next.document?.revision ?? -1
        if (nextRevision !== currentRevision) {
          setState(next)
          setMesh(await api<CadMesh>(`/api/projects/${projectId}/cad/mesh?edges=true`))
        }
      } catch {}
    }, 2500)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [projectId, busy, state?.document?.revision])

  useEffect(() => {
    let disposed = false
    let retry: number | undefined
    const connect = () => {
      if (disposed) return
      setConnection('connecting')
      const socket = new WebSocket(websocketUrl(`/api/ws/projects/${projectId}`))
      socketRef.current = socket
      socket.onopen = () => setConnection('online')
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data)
        if (message.type === 'state') setState(message.payload)
        if (message.type === 'mesh') setMesh(message.payload)
        if (message.type === 'busy') { setBusy(Boolean(message.payload)); if (!message.payload) setProgress(null) }
        if (message.type === 'progress') {
          const value = message.payload || {}
          setProgress({ message: translateCoreMessage(value.message || 'Working…', 'progress'), percent: Number(value.percent || 5) })
        }
        if (message.type === 'result') {
          if (message.payload?.state) setState(message.payload.state)
          setMessages((current) => [...current, { role: 'assistant', content: translateCoreMessage(message.payload?.message || 'Operation completed.', 'result') }])
        }
        if (message.type === 'error') {
          const text = translateCoreMessage(message.payload || 'Unknown error', 'error')
          setMessages((current) => [...current, { role: 'error', content: text }])
          showError(text)
        }
      }
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null
        setConnection('offline')
        if (!disposed) retry = window.setTimeout(connect, 1800)
      }
      socket.onerror = () => socket.close()
    }
    connect()
    return () => { disposed = true; if (retry) clearTimeout(retry); socketRef.current?.close() }
  }, [projectId, showError])

  async function command(commandName: string, argumentsValue: Record<string, unknown> = {}) {
    try {
      const response = await api<{ state: CadState }>(`/api/projects/${projectId}/cad/command`, { method: 'POST', body: JSON.stringify({ command: commandName, arguments: argumentsValue }) })
      setState(response.state)
      if (response.state.document?.view_orientation) setOrientation(response.state.document.view_orientation)
      await loadMesh()
    } catch (reason) { showError(reason) }
  }

  async function activateDocument(documentId: string) {
    try {
      const response = await api<{ state: CadState }>(`/api/projects/${projectId}/cad/documents/${documentId}/activate`, { method: 'POST' })
      setState(response.state); setOrientation(response.state.document?.view_orientation || 'iso_top_right'); await loadMesh()
    } catch (reason) { showError(reason) }
  }

  async function select(selection: Selection) {
    try {
      const response = await api<{ state: CadState }>(`/api/projects/${projectId}/cad/select`, { method: 'POST', body: JSON.stringify(selection) })
      setState(response.state)
    } catch (reason) { showError(reason) }
  }

  async function save() {
    try {
      const response = await api<{ state: CadState }>(`/api/projects/${projectId}/cad/save`, { method: 'POST' })
      setState(response.state)
    } catch (reason) { showError(reason) }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const form = new FormData(); form.append('file', file)
    try {
      setBusy(true); setProgress({ message: 'Opening CAD file…', percent: 35 })
      const response = await api<{ state: CadState }>(`/api/projects/${projectId}/cad/import`, { method: 'POST', body: form })
      setState(response.state); setOrientation(response.state.document?.view_orientation || 'iso_top_right'); await loadMesh()
    } catch (reason) { showError(reason) }
    finally { setBusy(false); setProgress(null) }
  }

  function sendPrompt(prompt: string) {
    if (!(account?.connected || account?.account)) { setShowConnect(true); return }
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) { showError('The live CAD connection is offline. Please try again in a moment.'); return }
    setMessages((current) => [...current, { role: 'user', content: prompt }])
    setBusy(true); setProgress({ message: 'Preparing request…', percent: 2 })
    socket.send(JSON.stringify({ type: 'prompt', payload: { prompt, model: selectedModel || null, effort } }))
  }

  function cancelPrompt() { socketRef.current?.send(JSON.stringify({ type: 'cancel' })) }

  function changeTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next); localStorage.setItem('cadia-theme', next)
  }

  function setView(value: string) {
    setOrientation(value); setViewRevision((current) => current + 1)
    void command('set_view_orientation', { orientation: value, fit: true })
  }

  function captureView() {
    const canvas = document.querySelector<HTMLCanvasElement>('.cad-canvas canvas')
    if (!canvas) { showError('The 3D viewport is not ready to capture yet.'); return }
    canvas.toBlob((blob) => {
      if (!blob) { showError('Could not create a PNG capture.'); return }
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `${project?.name || 'standalone-cad'}-view.png`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    }, 'image/png')
  }

  const selectedLabel = state?.selection.type === 'face' ? state.selection.face_ref : state?.selection.type === 'edge' ? state.selection.edge_ref : state?.selection.type === 'part' ? (state.document?.title || 'Part') : state?.selection.feature_name || state?.selection.occurrence_name
  const volume = mesh?.stats.volume_mm3 || 0

  return (
    <main className={`cad-app ${theme}`}>
      <header className="cad-topbar">
        <button className="brand-lockup compact" onClick={() => navigate('/')}><span className="brand-mark">C</span><span>CAD<b>ia</b></span></button>
        <div className="project-breadcrumb"><span>{project?.name || 'Opening project…'}</span><ChevronDown size={14} /></div>
        <nav className="main-menu"><button>File</button><button>Edit</button><button>View</button><button>Tools</button></nav>
        <div className="topbar-spacer" />
        <span className={`sync-state ${connection}`}><Cloud size={14} /> {connection === 'online' ? 'Saved / online' : connection === 'connecting' ? 'Connecting' : 'Reconnecting'}</span>
        <button className={`codex-badge ${account?.connected || account?.account ? 'connected' : ''}`} onClick={() => setShowConnect(true)}><span />{account?.connected || account?.account ? `${account?.providerLabel || account?.account?.planType || 'AI'}${account?.provider === 'codex' && usage !== null ? ` · ${usage}% used` : ''}` : 'Connect AI'}</button>
        <button className="icon-button" onClick={changeTheme} title="Change theme">{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}</button>
        <div className="profile-menu"><span>{user.display_name.slice(0, 1)}</span><div><strong>{user.display_name}</strong><small>{user.email}</small></div><button onClick={onLogout} title="Sign out"><LogOut size={15} /></button></div>
      </header>
      <div className="cad-toolbar">
        <div className="tool-group"><button title="New Part" onClick={() => command('new_part', { name: 'Untitled' })}><Box size={18} /><span>New Part</span></button><button title="New Assembly" onClick={() => command('new_assembly', { name: 'Untitled Assembly' })}><Layers3 size={18} /><span>Assembly</span></button><button title="File Open" onClick={() => uploadRef.current?.click()}><FolderOpen size={18} /><span>Open</span></button><input ref={uploadRef} hidden type="file" accept=".scad.json,.step,.stp,.brep,.brp" onChange={upload} /><button title="Save" onClick={save}><Save size={18} /><span>Save</span></button></div>
        <div className="tool-separator" />
        <div className="tool-group icon-only"><button disabled={!state?.undo_available || busy} title="Undo" onClick={() => command('undo')}><Undo2 size={18} /></button><button disabled={!state?.redo_available || busy} title="Redo" onClick={() => command('redo')}><Redo2 size={18} /></button></div>
        <div className="tool-separator" />
        <div className="tool-group segmented"><button className={selectionMode === 'face' ? 'active' : ''} onClick={() => setSelectionMode('face')}><SquareMousePointer size={17} />Face</button><button className={selectionMode === 'edge' ? 'active' : ''} onClick={() => setSelectionMode('edge')}><Waypoints size={17} />Edge</button><button className={selectionMode === 'object' ? 'active' : ''} onClick={() => setSelectionMode('object')}><MousePointer2 size={17} />Object</button></div>
        <div className="tool-separator" />
        <div className="tool-group icon-only"><button className={showGrid ? 'active' : ''} title="Grid" onClick={() => setShowGrid(!showGrid)}><Grid3X3 size={18} /></button><button className={showEdges ? 'active' : ''} title="Show edges" onClick={() => setShowEdges(!showEdges)}><ScanLine size={18} /></button><button className={transparent ? 'active' : ''} title="Transparency" onClick={() => setTransparent(!transparent)}><View size={18} /></button></div>
        <div className="tool-separator" />
        <div className="tool-group section-tools"><Scissors size={17} /><select value={sectionAxis} onChange={(event) => setSectionAxis(event.target.value as any)}><option value="off">Section off</option><option value="x">X section</option><option value="y">Y section</option><option value="z">Z section</option></select>{sectionAxis !== 'off' && <input type="range" min="0" max="1" step="0.01" value={sectionOffset} onChange={(event) => setSectionOffset(Number(event.target.value))} />}</div>
        <div className="topbar-spacer" />
        <div className="tool-group export-menu"><button onClick={() => setShowManufacturing(true)} title="Manufacturing"><Factory size={16} />Manufacture</button><button onClick={() => download(`/api/projects/${projectId}/cad/download`)}><Download size={17} />SCAD</button><button onClick={() => download(`/api/projects/${projectId}/cad/export/step`)}>STEP</button><button onClick={() => download(`/api/projects/${projectId}/cad/export/stl`)}>STL</button></div>
      </div>
      <div className="cad-body">
        <FeatureTree state={state} tab={treeTab} onTab={setTreeTab} onSelect={select} onActivate={activateDocument} />
        <section className="viewport-shell">
          <CadViewer mesh={mesh} selection={state?.selection || {}} selectionMode={selectionMode} showEdges={showEdges} showGrid={showGrid} transparent={transparent} sectionAxis={sectionAxis} sectionOffset={sectionOffset} orientation={`${orientation}|${viewRevision}`} theme={theme} onSelect={select} />
          <div className="view-toolbar"><button onClick={() => { setViewRevision((value) => value + 1) }} title="Fit view"><Maximize2 size={16} /></button><button onClick={captureView} title="Save current view as PNG"><Camera size={15} /></button>{orientations.map(([value, label]) => <button key={value} className={orientation === value ? 'active' : ''} onClick={() => setView(value)} title={value}>{label}</button>)}</div>
          <div className="axis-origin"><span className="x">X</span><span className="y">Y</span><span className="z">Z</span></div>
        </section>
        <ChatPanel messages={messages} busy={busy} progress={progress} account={account} models={models} selectedModel={selectedModel} selectedEffort={effort} onModel={setSelectedModel} onEffort={setEffort} onConnect={() => setShowConnect(true)} onSend={sendPrompt} onCancel={cancelPrompt} />
      </div>
      <footer className="cad-statusbar">
        <span className="kernel-ready"><i /> B-REP READY</span><span><Workflow size={13} /> {state?.document?.type?.toUpperCase() || 'NO DOCUMENT'}</span><span>Revision {state?.document?.revision ?? 0}</span><span>{mesh?.stats.faces ?? 0} faces · {mesh?.stats.edges ?? 0} edges</span><span>Volume {volume.toLocaleString(undefined, { maximumFractionDigits: 2 })} mm³</span><span className="status-spacer" />{selectedLabel ? <span className="selected-status"><SquareMousePointer size={13} /> {String(selectedLabel).slice(0, 30)}</span> : <span>No selection</span>}<span>mm</span>
      </footer>
      {showConnect && <AIConnectModal account={account} onAccount={(next) => { setAccount(next); if (next?.connected || next?.account) void loadAccount() }} onClose={() => setShowConnect(false)} />}
      {showManufacturing && <ManufacturingPanel projectId={projectId} onClose={() => setShowManufacturing(false)} onError={showError} />}
      {error && <div className="toast-error"><X size={16} /><span>{error}</span><button onClick={() => setError('')}><X size={14} /></button></div>}
    </main>
  )
}
