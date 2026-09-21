import { useEffect, useState } from 'react'
import { CheckCircle2, Download, Factory, Printer, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { api, download } from '../api'

export type ManufacturingCapabilities = {
  native: {
    dfm_mesh_precheck: boolean
    three_mf_core: boolean
    step_ap242: boolean
  }
  optional: {
    lib3mf: Record<string, unknown>
    slicer: { backend?: string; available?: boolean; detail?: string; license_boundary?: string }
    cam: { available?: boolean; library?: string; license?: string; detail?: string }
  }
  printers: Array<{ name: string; kind: string; configured: boolean; execution_enabled: boolean }>
  safety: string
}

type DfmReport = {
  ok: boolean
  scope: string
  checks: Array<{ id: string; status: 'pass' | 'warning' | 'fail'; message: string; value?: unknown }>
  metrics?: Record<string, unknown>
  limitations?: string[]
}

type Props = {
  projectId: string
  onClose: () => void
  onError: (reason: unknown) => void
}

function statusIcon(status: string) {
  return status === 'pass' ? <CheckCircle2 size={15} /> : <TriangleAlert size={15} />
}

async function postDownload(path: string, body: unknown, fallbackName: string) {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const payload = await response.json()
      message = payload.detail || payload.message || message
    } catch {}
    throw new Error(String(message))
  }
  const blob = await response.blob()
  const disposition = response.headers.get('content-disposition') || ''
  const matched = disposition.match(/filename\*?=(?:UTF-8''|\")?([^";]+)/i)
  const filename = matched ? decodeURIComponent(matched[1].replace(/"/g, '')) : fallbackName
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1500)
}

export default function ManufacturingPanel({ projectId, onClose, onError }: Props) {
  const [capabilities, setCapabilities] = useState<ManufacturingCapabilities | null>(null)
  const [dfm, setDfm] = useState<DfmReport | null>(null)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    api<ManufacturingCapabilities>(`/api/projects/${projectId}/manufacturing/capabilities`)
      .then(setCapabilities)
      .catch(onError)
  }, [projectId, onError])

  async function runDfm() {
    try {
      setBusy('dfm')
      setDfm(await api<DfmReport>(`/api/projects/${projectId}/manufacturing/dfm`))
    } catch (reason) { onError(reason) }
    finally { setBusy('') }
  }

  async function slice() {
    try {
      setBusy('slice')
      await postDownload(`/api/projects/${projectId}/manufacturing/slice`, { overrides: {} }, 'cadia.gcode')
    } catch (reason) { onError(reason) }
    finally { setBusy('') }
  }

  async function upload(target: string) {
    try {
      setBusy(`printer:${target}`)
      await api(`/api/projects/${projectId}/manufacturing/slice-upload/${encodeURIComponent(target)}`, {
        method: 'POST', body: JSON.stringify({ overrides: {}, start: false }),
      })
    } catch (reason) { onError(reason) }
    finally { setBusy('') }
  }

  const slicer = capabilities?.optional.slicer

  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="modal-card manufacturing-modal">
        <button className="modal-close" onClick={onClose}><X size={18} /></button>
        <div className="manufacturing-heading"><span><Factory size={20} /></span><div><h2>Manufacturing</h2><p>Export manufacturing files, run deterministic print checks, and generate real G-code from the current CAD model.</p></div></div>

        <div className="manufacturing-grid">
          <article className="manufacturing-card">
            <strong>Manufacturing Files</strong>
            <p>Generate STEP AP242 from the exact OCCT B-Rep and 3MF Core from the tessellated web mesh.</p>
            <div className="manufacturing-actions">
              <button className="secondary" onClick={() => download(`/api/projects/${projectId}/manufacturing/export/step-ap242`)}><Download size={14} />STEP AP242</button>
              <button className="secondary" onClick={() => download(`/api/projects/${projectId}/manufacturing/export/3mf`)}><Download size={14} />3MF</button>
            </div>
          </article>

          <article className="manufacturing-card">
            <strong>3D Print DFM</strong>
            <p>Check mesh closure, non-manifold edges, build-volume fit, overhang risk, and basic size limits before slicing.</p>
            <button className="secondary" disabled={Boolean(busy)} onClick={runDfm}>{busy === 'dfm' ? 'Checking…' : 'Run DFM Precheck'}</button>
          </article>

          <article className="manufacturing-card">
            <strong>G-code</strong>
            <p>{slicer?.available ? `${slicer.backend} slicer is ready.` : slicer?.detail || 'Configure PrusaSlicer or CuraEngine to generate G-code.'}</p>
            <button className="secondary" disabled={!slicer?.available || Boolean(busy)} onClick={slice}><Download size={14} />{busy === 'slice' ? 'Slicing…' : 'Generate G-code'}</button>
          </article>

          <article className="manufacturing-card">
            <strong>Printer Delivery</strong>
            <p>Upload G-code only to administrator-configured OctoPrint or Moonraker targets. Automatic machine start remains disabled by default.</p>
            <div className="manufacturing-actions">
              {(capabilities?.printers || []).map((printer) => <button className="secondary" key={printer.name} disabled={!slicer?.available || Boolean(busy)} onClick={() => upload(printer.name)}><Printer size={14} />{busy === `printer:${printer.name}` ? 'Uploading…' : `Upload to ${printer.name}`}</button>)}
              {capabilities && capabilities.printers.length === 0 && <span className="manufacturing-muted">No printer configured</span>}
            </div>
          </article>
        </div>

        {dfm && <div className={`dfm-report ${dfm.ok ? 'ok' : 'warn'}`}>
          <div className="dfm-title"><ShieldCheck size={16} /><strong>DFM Results</strong><span>{dfm.scope}</span></div>
          {dfm.checks.map((check) => <div className={`dfm-check ${check.status}`} key={check.id}>{statusIcon(check.status)}<span>{check.message}</span></div>)}
          {dfm.limitations?.length ? <p className="dfm-limit">This is a fast mesh precheck. Final printability, wall thickness, supports, time, and material usage must be verified with the selected slicer and machine profile.</p> : null}
        </div>}

        <div className="manufacturing-safety"><ShieldCheck size={14} /><span>{capabilities?.safety || 'Automatic machine execution is disabled by default.'}</span></div>
      </section>
    </div>
  )
}
