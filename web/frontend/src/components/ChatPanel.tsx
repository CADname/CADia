import { KeyboardEvent, useEffect, useRef, useState } from 'react'
import { Bot, CircleStop, CornerDownLeft, Gauge, Link2, Send, Sparkles, UserRound } from 'lucide-react'
import type { AccountInfo, ChatMessage, ModelInfo } from '../types'

type Props = {
  messages: ChatMessage[]
  busy: boolean
  progress: { message: string; percent: number } | null
  account: AccountInfo | null
  models: ModelInfo[]
  selectedModel: string
  selectedEffort: string
  onModel: (value: string) => void
  onEffort: (value: string) => void
  onConnect: () => void
  onSend: (prompt: string) => void
  onCancel: () => void
}

const examples = [
  ['Spur Gear', 'Create a spur gear with module 2, 25 teeth, a 20 mm face width, a 15 mm bore, and a 20 degree pressure angle.'],
  ['Mounting Plate', 'Create an 80 x 60 x 8 mm mounting plate with four 6 mm holes positioned 10 mm from each corner.'],
  ['Lead Screw + Nut', 'Create a lead screw and matching nut with a 20 mm nominal diameter, 4 mm pitch, and 30 degree thread angle.'],
  ['Piston Assembly', 'Create a piston and cylinder assembly with a 30 mm bore, 40 mm stroke, and a 10 mm rod.'],
]

export default function ChatPanel(props: Props) {
  const { messages, busy, progress, account, models, selectedModel, selectedEffort, onModel, onEffort, onConnect, onSend, onCancel } = props
  const [prompt, setPrompt] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [messages, progress])
  const selected = models.find((model) => (model.model || model.id) === selectedModel)
  const efforts = selected?.supportedReasoningEfforts || []
  const effortOptions = efforts.length ? efforts.map((item) => item.reasoningEffort) : ['low', 'medium', 'high']
  useEffect(() => {
    if (efforts.length && !effortOptions.includes(selectedEffort)) {
      onEffort(selected?.defaultReasoningEffort || effortOptions[0])
    }
  }, [efforts, effortOptions, onEffort, selected?.defaultReasoningEffort, selectedEffort])

  const effortLabel = (value: string) => value === 'low' ? 'Fast' : value === 'medium' ? 'Balanced' : value === 'high' ? 'Maximum' : value

  function submit() {
    const text = prompt.trim()
    if (!text || busy) return
    setPrompt('')
    onSend(text)
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <aside className="ai-panel">
      <div className="ai-header"><div><span className="ai-glyph"><Sparkles size={16} /></span><div><strong>CAD AI</strong><small>{account?.connected || account?.account ? `${account?.providerLabel || account?.account?.planType || 'AI'} connected` : 'AI not connected'}</small></div></div><button className={`connection-dot ${account?.connected || account?.account ? 'online' : ''}`} title="Connect AI" onClick={onConnect}><Link2 size={16} /></button></div>
      <div className="model-controls">
        <select value={selectedModel} onChange={(event) => onModel(event.target.value)} disabled={!models.length}>{models.length ? models.map((model) => <option key={model.id} value={model.model || model.id}>{model.displayName || model.model || model.id}</option>) : <option>Auto-select model</option>}</select>
        {account?.supportsReasoning !== false && <select value={effortOptions.includes(selectedEffort) ? selectedEffort : effortOptions[0]} onChange={(event) => onEffort(event.target.value)}>{effortOptions.map((value) => <option value={value} key={value}>{effortLabel(value)}</option>)}</select>}
      </div>
      <div className="chat-scroll">
        {messages.length === 0 && <div className="chat-welcome"><span><Bot size={22} /></span><h3>What should we design?</h3><p>Describe dimensions, design intent, and follow-up edits in natural language. CADia keeps the result editable as parametric CAD, not a disposable mesh.</p><div className="prompt-examples">{examples.map(([label, value]) => <button key={label} onClick={() => setPrompt(value)}>{label}</button>)}</div></div>}
        {messages.map((message, index) => <div className={`chat-message ${message.role}`} key={message.id || index}><span className="message-avatar">{message.role === 'user' ? <UserRound size={15} /> : message.role === 'error' ? '!' : <Sparkles size={15} />}</span><div><small>{message.role === 'user' ? 'You' : message.role === 'error' ? 'Error' : 'CAD AI'}</small><p>{message.content}</p></div></div>)}
        {busy && <div className="chat-message assistant working"><span className="message-avatar"><Sparkles size={15} /></span><div><small>Working</small><p>{progress?.message || 'Preparing the request…'}</p><div className="progress-track"><span style={{ width: `${progress?.percent || 3}%` }} /></div><em>Estimated progress {progress?.percent || 3}%</em></div></div>}
        <div ref={endRef} />
      </div>
      {!(account?.connected || account?.account) && <button className="connect-strip" onClick={onConnect}><Link2 size={16} /><span><strong>Connect AI</strong><small>ChatGPT/Codex · GitHub Copilot · Claude · Gemini · OpenAI API</small></span></button>}
      <div className="prompt-box">
        <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={keyDown} placeholder="Example: Make the selected cylindrical face 24 mm in diameter" rows={3} disabled={busy} />
        <div className="prompt-footer"><span><CornerDownLeft size={13} /> Enter to send · Shift+Enter for a new line</span>{busy ? <button className="stop-button" onClick={onCancel}><CircleStop size={18} /></button> : <button className="send-button" onClick={submit} disabled={!prompt.trim()}><Send size={18} /></button>}</div>
      </div>
      <div className="ai-foot"><Gauge size={13} /> Verifier · Atomic rollback · Auto recovery</div>
    </aside>
  )
}
