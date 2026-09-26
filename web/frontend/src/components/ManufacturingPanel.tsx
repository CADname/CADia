import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Download, Factory, PackageCheck, Printer, ShieldCheck, TriangleAlert, Truck, X } from 'lucide-react'
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
  fulfillment?: {
    slant3d?: {
      provider?: string
      enabled?: boolean
      demo_mode?: boolean
      checkout_enabled?: boolean
      mcp_url?: string
      transport?: string
      build_volume_mm?: { x: number; y: number; z: number }
    }
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

type QuoteResponse = {
  provider: string
  demo_mode: boolean
  model?: { filename?: string; bytes?: number; sha256?: string; size_mm?: unknown }
  dfm?: DfmReport
  quote?: unknown
}

type Props = {
  projectId: string
  onClose: () => void
  onError: (reason: unknown) => void
}

type ProviderMaterial = { id: string; material: string; color: string; hex?: string }
type Address = { name: string; email: string; line1: string; line2: string; city: string; state: string; postal_code: string; country: string }

const COUNTRIES: Array<[string, string]> = [
  ['US', 'United States'], ['KR', 'South Korea'], ['CA', 'Canada'], ['GB', 'United Kingdom'], ['JP', 'Japan'], ['CN', 'China'],
  ['TW', 'Taiwan'], ['HK', 'Hong Kong'], ['SG', 'Singapore'], ['AU', 'Australia'], ['NZ', 'New Zealand'], ['DE', 'Germany'],
  ['FR', 'France'], ['IT', 'Italy'], ['ES', 'Spain'], ['NL', 'Netherlands'], ['BE', 'Belgium'], ['CH', 'Switzerland'],
  ['AT', 'Austria'], ['SE', 'Sweden'], ['NO', 'Norway'], ['DK', 'Denmark'], ['FI', 'Finland'], ['IE', 'Ireland'], ['PL', 'Poland'],
  ['CZ', 'Czechia'], ['PT', 'Portugal'], ['GR', 'Greece'], ['RO', 'Romania'], ['HU', 'Hungary'], ['AE', 'United Arab Emirates'],
  ['SA', 'Saudi Arabia'], ['IL', 'Israel'], ['IN', 'India'], ['TH', 'Thailand'], ['VN', 'Vietnam'], ['MY', 'Malaysia'],
  ['ID', 'Indonesia'], ['PH', 'Philippines'], ['MX', 'Mexico'], ['BR', 'Brazil'], ['AR', 'Argentina'], ['CL', 'Chile'], ['ZA', 'South Africa'],
]

const MATERIAL_HELP: Record<string, string> = {
  PLA: 'General prototypes and indoor parts.',
  PETG: 'Tougher functional parts with better moisture resistance.',
  'CF PETG': 'Stiffer functional parts when extra rigidity is useful.',
  OPM: 'Outdoor-oriented material for heat, chemicals, and long-term exposure.',
}

function statusIcon(status: string) {
  return status === 'pass' ? <CheckCircle2 size={15} /> : <TriangleAlert size={15} />
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function findString(value: unknown, wanted: string[]): string {
  if (typeof value === 'string') {
    const wantsQuote = wanted.some((key) => key.toLowerCase().includes('quote'))
    if (wantsQuote) {
      const match = value.match(/quote[ _-]?id[\s:="']+([A-Za-z0-9._:-]+)/i)
      if (match?.[1]) return match[1]
    }
    return ''
  }
  if (!value || typeof value !== 'object') return ''
  const record = value as Record<string, unknown>
  for (const key of wanted) {
    const candidate = record[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
    if (typeof candidate === 'number') return String(candidate)
  }
  for (const candidate of Object.values(record)) {
    if (candidate && typeof candidate === 'object') {
      const nested = findString(candidate, wanted)
      if (nested) return nested
    }
  }
  return ''
}

function findNumber(value: unknown, wanted: string[]): number | null {
  if (Array.isArray(value)) {
    for (const item of value) {
      const nested = findNumber(item, wanted)
      if (nested != null) return nested
    }
    return null
  }
  const record = asRecord(value)
  if (!record) return null
  for (const key of wanted) {
    const candidate = record[key]
    if (typeof candidate === 'number' && Number.isFinite(candidate)) return candidate
    if (typeof candidate === 'string' && candidate.trim() && Number.isFinite(Number(candidate))) return Number(candidate)
  }
  for (const candidate of Object.values(record)) {
    if (candidate && typeof candidate === 'object') {
      const nested = findNumber(candidate, wanted)
      if (nested != null) return nested
    }
  }
  return null
}

function findArray(value: unknown, wanted: string[]): unknown[] {
  if (Array.isArray(value)) return value
  const record = asRecord(value)
  if (!record) return []
  for (const key of wanted) {
    const candidate = record[key]
    if (Array.isArray(candidate)) return candidate
  }
  for (const candidate of Object.values(record)) {
    if (candidate && typeof candidate === 'object') {
      const nested = findArray(candidate, wanted)
      if (nested.length) return nested
    }
  }
  return []
}

function normalizeMaterials(value: unknown): ProviderMaterial[] {
  const root = asRecord(value)
  const rows = Array.isArray(value) ? value : Array.isArray(root?.materials) ? root.materials : []
  const seen = new Set<string>()
  return rows.flatMap((row) => {
    const record = asRecord(row)
    if (!record) return []
    const id = typeof record.id === 'string' ? record.id : ''
    const material = typeof record.material === 'string' ? record.material.trim() : ''
    const color = typeof record.color === 'string' ? record.color.trim() : ''
    const hex = typeof record.hex === 'string' ? record.hex : undefined
    if (!material || !color) return []
    const key = `${material.toLowerCase()}::${color.toLowerCase()}`
    if (seen.has(key)) return []
    seen.add(key)
    return [{ id, material, color, hex }]
  })
}

function formatUsd(value: number | null): string {
  return value == null ? '—' : `$${value.toFixed(2)}`
}

function formatSize(value: unknown): string {
  if (Array.isArray(value) && value.length >= 3) {
    const nums = value.slice(0, 3).map(Number)
    if (nums.every(Number.isFinite)) return `${nums[0]} × ${nums[1]} × ${nums[2]} mm`
  }
  const record = asRecord(value)
  if (record) {
    const x = Number(record.x ?? record.X ?? record.width)
    const y = Number(record.y ?? record.Y ?? record.depth)
    const z = Number(record.z ?? record.Z ?? record.height)
    if ([x, y, z].every(Number.isFinite)) return `${x} × ${y} × ${z} mm`
  }
  return 'Current model'
}

function errorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message
  if (typeof reason === 'string' && reason.trim()) return reason
  return 'The request could not be completed.'
}

function localeCountry(): string {
  if (typeof navigator === 'undefined') return 'US'
  const locale = navigator.language || ''
  const match = locale.match(/[-_]([A-Za-z]{2})$/)
  const code = match?.[1]?.toUpperCase()
  return code && COUNTRIES.some(([item]) => item === code) ? code : 'US'
}

function validateAddress(address: Address): string | null {
  if (!address.name.trim()) return 'Enter the recipient name.'
  const email = address.email.trim()
  if (!email) return 'Enter an email address.'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return 'Enter a valid email address.'
  if (!address.line1.trim()) return 'Enter the street address.'
  if (!address.city.trim()) return 'Enter the city.'
  const state = address.state.trim()
  if (state.length < 2) return 'Enter a state, province, or region (at least 2 characters).'
  const postal = address.postal_code.trim()
  if (postal.length < 3) return 'Enter a valid postal code (at least 3 characters).'
  if (!/^[A-Z]{2}$/.test(address.country)) return 'Choose a country.'
  return null
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
      const detail = payload?.detail ?? payload?.message
      if (typeof detail === 'string') message = detail
    } catch {}
    throw new Error(message)
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

  const [materials, setMaterials] = useState<unknown>(null)
  const [materialsState, setMaterialsState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [materialsError, setMaterialsError] = useState('')
  const [material, setMaterial] = useState('PLA')
  const [color, setColor] = useState('black')

  const [quote, setQuote] = useState<QuoteResponse | null>(null)
  const [quantity, setQuantity] = useState(1)
  const [pricedQuantity, setPricedQuantity] = useState<number | null>(null)
  const [quantityResult, setQuantityResult] = useState<unknown>(null)
  const [shippingResult, setShippingResult] = useState<unknown>(null)
  const [shippingError, setShippingError] = useState('')
  const [selectedShipping, setSelectedShipping] = useState('')
  const [address, setAddress] = useState<Address>(() => ({
    name: '', email: '', line1: '', line2: '', city: '', state: '', postal_code: '', country: localeCountry(),
  }))

  useEffect(() => {
    let active = true
    api<ManufacturingCapabilities>(`/api/projects/${projectId}/manufacturing/capabilities`)
      .then((value) => { if (active) setCapabilities(value) })
      .catch(onError)
    return () => { active = false }
  }, [projectId, onError])

  const slicer = capabilities?.optional.slicer
  const fulfillment = capabilities?.fulfillment?.slant3d
  const materialRows = useMemo(() => normalizeMaterials(materials), [materials])
  const materialNames = useMemo(() => Array.from(new Set(materialRows.map((row) => row.material))), [materialRows])
  const colorRows = useMemo(() => materialRows.filter((row) => row.material === material), [materialRows, material])
  const selectedMaterial = useMemo(
    () => colorRows.find((row) => row.color.toLowerCase() === color.toLowerCase()) || colorRows[0] || null,
    [colorRows, color],
  )

  const quoteId = useMemo(() => findString(quote?.quote, ['quote_id', 'quoteId', 'quoteID']), [quote])
  const quotePrice = useMemo(() => findNumber(quote?.quote, ['set_price_usd', 'total_price_usd', 'subtotal_usd', 'price_usd']), [quote])
  const quotePartName = useMemo(() => findString(quote?.quote, ['part_name', 'filename', 'name']), [quote])
  const quantityUnitPrice = useMemo(() => findNumber(quantityResult, ['unit_price_usd', 'unit_price', 'price_per_part_usd', 'price_usd']), [quantityResult])
  const quantitySubtotal = useMemo(() => findNumber(quantityResult, ['subtotal_usd', 'subtotal', 'total_price_usd', 'total', 'set_price_usd']), [quantityResult])
  const quantitySetupFee = useMemo(() => findNumber(quantityResult, ['setup_fee_usd', 'setup_fee', 'setup']), [quantityResult])
  const quantityLeadTime = useMemo(() => findString(quantityResult, ['lead_time', 'leadTime', 'lead_time_days']), [quantityResult])
  const shippingOptions = useMemo(() => findArray(shippingResult, ['shipping_options', 'options', 'rates', 'services']), [shippingResult])
  const displaySubtotal = quantitySubtotal ?? (pricedQuantity === 1 ? quotePrice : null)
  const displayUnitPrice = quantityUnitPrice ?? (pricedQuantity === 1 ? quotePrice : null)

  async function loadMaterials(showGlobalError = false) {
    try {
      setMaterialsState('loading')
      setMaterialsError('')
      const response = await api<{ materials: unknown }>(`/api/projects/${projectId}/manufacturing/fulfillment/slant3d/materials`)
      setMaterials(response.materials)
      const rows = normalizeMaterials(response.materials)
      if (!rows.length) throw new Error('Slant 3D returned no available material/color combinations.')
      const selected = rows.find((row) => row.material.toLowerCase() === 'pla' && row.color.toLowerCase() === 'black')
        || rows.find((row) => row.material.toLowerCase() === 'pla')
        || rows[0]
      setMaterial(selected.material)
      setColor(selected.color)
      setMaterialsState('ready')
    } catch (reason) {
      setMaterialsState('error')
      setMaterialsError(errorMessage(reason))
      if (showGlobalError) onError(reason)
    }
  }

  useEffect(() => {
    if (fulfillment?.enabled && materialsState === 'idle') void loadMaterials(false)
  }, [fulfillment?.enabled, materialsState])

  function resetProviderResults() {
    setQuote(null)
    setQuantityResult(null)
    setPricedQuantity(null)
    setShippingResult(null)
    setShippingError('')
    setSelectedShipping('')
  }

  function changeMaterial(next: string) {
    setMaterial(next)
    const first = materialRows.find((row) => row.material === next)
    setColor(first?.color || '')
    resetProviderResults()
  }

  function changeColor(next: string) {
    setColor(next)
    resetProviderResults()
  }

  function changeQuantity(next: number) {
    const value = Math.min(100000, Math.max(1, Math.floor(next || 1)))
    setQuantity(value)
    if (pricedQuantity !== value) {
      setQuantityResult(null)
      setPricedQuantity(null)
      setShippingResult(null)
      setSelectedShipping('')
    }
  }

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

  async function updateQuantity(targetQuoteId = quoteId, targetQuantity = quantity) {
    if (!targetQuoteId) throw new Error('The production quote did not contain a quote ID.')
    const response = await api(`/api/projects/${projectId}/manufacturing/fulfillment/slant3d/quantity`, {
      method: 'POST', body: JSON.stringify({ quote_id: targetQuoteId, quantity: targetQuantity }),
    })
    setQuantityResult(response)
    setPricedQuantity(targetQuantity)
    setShippingResult(null)
    setSelectedShipping('')
    return response
  }

  async function getLiveQuote() {
    if (materialsState !== 'ready' || !selectedMaterial) {
      if (materialsState === 'error') void loadMaterials(true)
      else onError(new Error('Live Slant 3D materials are still loading.'))
      return
    }
    try {
      setBusy('quote')
      resetProviderResults()
      const response = await api<QuoteResponse>(`/api/projects/${projectId}/manufacturing/fulfillment/slant3d/quote`, {
        method: 'POST',
        body: JSON.stringify({
          filament_id: selectedMaterial.id || null,
          material: selectedMaterial.material,
          color: selectedMaterial.color,
        }),
      })
      setQuote(response)
      const id = findString(response.quote, ['quote_id', 'quoteId', 'quoteID'])
      if (!id) throw new Error('Slant 3D returned pricing but no quote ID for follow-up pricing/shipping.')
      if (quantity === 1) {
        setPricedQuantity(1)
      } else {
        await updateQuantity(id, quantity)
      }
    } catch (reason) { onError(reason) }
    finally { setBusy('') }
  }

  async function repriceQuantity() {
    try {
      setBusy('quantity')
      await updateQuantity()
    } catch (reason) { onError(reason) }
    finally { setBusy('') }
  }

  async function getShipping() {
    if (!quoteId) return setShippingError('Get a production quote first.')
    const validation = validateAddress(address)
    if (validation) return setShippingError(validation)
    try {
      setBusy('shipping')
      setShippingError('')
      if (pricedQuantity !== quantity) await updateQuantity(quoteId, quantity)
      const response = await api(`/api/projects/${projectId}/manufacturing/fulfillment/slant3d/shipping`, {
        method: 'POST',
        body: JSON.stringify({
          quote_id: quoteId,
          address: {
            name: address.name.trim(),
            email: address.email.trim(),
            line1: address.line1.trim(),
            line2: address.line2.trim() || null,
            city: address.city.trim(),
            state: address.state.trim() || null,
            postal_code: address.postal_code.trim(),
            country: address.country,
          },
        }),
      })
      setShippingResult(response)
    } catch (reason) {
      setShippingResult(null)
      setShippingError(errorMessage(reason))
    } finally { setBusy('') }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="modal-card manufacturing-modal">
        <button className="modal-close" onClick={onClose}><X size={18} /></button>
        <div className="manufacturing-heading"><span><Factory size={20} /></span><div><h2>Manufacturing</h2><p>Export manufacturing files, validate printability, generate G-code, or send the current model into a live production quote and shipping workflow.</p></div></div>

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

          <article className="manufacturing-card manufacturing-fulfillment">
            <div className="fulfillment-title-row">
              <div><strong>Print &amp; Ship</strong><span className="fulfillment-live">LIVE PRODUCTION</span></div>
              <span>{fulfillment?.provider || 'Slant 3D'}</span>
            </div>
            <p>CADia exports the current B-Rep to STL, checks the 220 × 220 × 220 mm production envelope, and requests live print-farm pricing. Material inventory loads automatically.</p>

            <div className="fulfillment-steps" aria-label="Print and ship workflow">
              <span className="active"><b>1</b> Configure</span><i />
              <span className={quote ? 'active' : ''}><b>2</b> Quote</span><i />
              <span className={Boolean(shippingResult) ? 'active' : ''}><b>3</b> Shipping</span>
            </div>

            <div className="fulfillment-fields fulfillment-fields-three">
              <label>Material
                <select disabled={materialsState !== 'ready' || Boolean(busy)} value={material} onChange={(event) => changeMaterial(event.target.value)}>
                  {(materialNames.length ? materialNames : [material]).map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
              <label>Color
                <select disabled={materialsState !== 'ready' || Boolean(busy)} value={color} onChange={(event) => changeColor(event.target.value)}>
                  {(colorRows.length ? colorRows : [{ id: '', material, color }]).map((row, index) => <option key={`${row.id || row.color}-${index}`} value={row.color}>{row.color}</option>)}
                </select>
              </label>
              <label>Quantity
                <input type="number" min={1} max={100000} value={quantity} onChange={(event) => changeQuantity(Number(event.target.value))} />
              </label>
            </div>

            <div className="fulfillment-material-help">
              {selectedMaterial?.hex && <span className="fulfillment-swatch" style={{ background: selectedMaterial.hex }} />}
              <span>{MATERIAL_HELP[material] || 'Live material/color availability from the production provider.'}</span>
              {materialsState === 'loading' && <em>Loading live inventory…</em>}
              {materialsState === 'ready' && <em>{materialRows.length} live combinations available</em>}
            </div>

            {materialsState === 'error' && <div className="fulfillment-inline-error"><TriangleAlert size={14} /><span>{materialsError}</span><button onClick={() => loadMaterials(true)}>Retry</button></div>}

            <div className="manufacturing-actions fulfillment-primary-action">
              <button className="secondary" disabled={!fulfillment?.enabled || materialsState !== 'ready' || Boolean(busy)} onClick={getLiveQuote}>
                {busy === 'quote' ? 'Getting production quote…' : quote ? 'Refresh Production Quote' : 'Get Production Quote'}
              </button>
              <span>Includes automatic DFM preflight and live Slant 3D pricing.</span>
            </div>
          </article>
        </div>

        {dfm && <div className={`dfm-report ${dfm.ok ? 'ok' : 'warn'}`}>
          <div className="dfm-title"><ShieldCheck size={16} /><strong>DFM Results</strong><span>{dfm.scope}</span></div>
          {dfm.checks.map((check) => <div className={`dfm-check ${check.status}`} key={check.id}>{statusIcon(check.status)}<span>{check.message}</span></div>)}
          {dfm.limitations?.length ? <p className="dfm-limit">This is a fast mesh precheck. Final printability, wall thickness, supports, time, and material usage must be verified with the selected slicer and machine profile.</p> : null}
        </div>}

        {quote && <div className="fulfillment-result">
          <div className="fulfillment-section-title"><PackageCheck size={17} /><div><strong>Production Quote</strong><span>Live provider pricing for the current model</span></div><b>{quote.provider}</b></div>

          <div className="fulfillment-quote-grid">
            <div><span>Estimated subtotal</span><strong>{formatUsd(displaySubtotal)}</strong></div>
            <div><span>Unit price</span><strong>{formatUsd(displayUnitPrice)}</strong></div>
            <div><span>Material</span><strong>{material} · {color}</strong></div>
            <div><span>Build size</span><strong>{formatSize(quote.model?.size_mm || asRecord(quote.quote)?.bounding_box_mm)}</strong></div>
          </div>

          <div className="fulfillment-provider-note"><CheckCircle2 size={14} /><span>{quotePartName || quote.model?.filename || 'Current model'} passed CADia preflight and received live Slant 3D pricing.</span></div>

          {quoteId ? <>
            <div className="fulfillment-price-controls">
              <div>
                <span>Quantity</span>
                <div className="fulfillment-qty-row">
                  {[1, 10, 100, 1000].map((preset) => <button key={preset} className={quantity === preset ? 'active' : ''} onClick={() => changeQuantity(preset)}>{preset}</button>)}
                  <input type="number" min={1} max={100000} value={quantity} onChange={(event) => changeQuantity(Number(event.target.value))} />
                </div>
              </div>
              <button className="secondary" disabled={Boolean(busy) || pricedQuantity === quantity} onClick={repriceQuantity}>{busy === 'quantity' ? 'Updating…' : pricedQuantity === quantity ? 'Price is current' : 'Update Price'}</button>
            </div>

            {(Boolean(quantityResult) || pricedQuantity === 1) && <div className="fulfillment-price-summary">
              <div><span>Quantity</span><strong>{pricedQuantity ?? quantity}</strong></div>
              <div><span>Unit price</span><strong>{formatUsd(displayUnitPrice)}</strong></div>
              <div><span>Subtotal</span><strong>{formatUsd(displaySubtotal)}</strong></div>
              <div><span>{quantitySetupFee != null ? 'Setup fee' : 'Lead time'}</span><strong>{quantitySetupFee != null ? formatUsd(quantitySetupFee) : quantityLeadTime || '—'}</strong></div>
              {quantitySetupFee != null && quantityLeadTime && <div><span>Lead time</span><strong>{quantityLeadTime}</strong></div>}
            </div>}

            <div className="fulfillment-divider" />
            <div className="fulfillment-section-title fulfillment-delivery-title"><Truck size={17} /><div><strong>Delivery</strong><span>Enter the destination to retrieve live carrier options.</span></div></div>

            <div className="fulfillment-address">
              <label>Recipient name<input value={address.name} onChange={(event) => setAddress({ ...address, name: event.target.value })} autoComplete="name" /></label>
              <label>Email<input type="email" value={address.email} onChange={(event) => setAddress({ ...address, email: event.target.value })} autoComplete="email" /></label>
              <label>Country
                <select value={address.country} onChange={(event) => { setAddress({ ...address, country: event.target.value }); setShippingResult(null); setShippingError('') }}>
                  {COUNTRIES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
                </select>
              </label>
              <label className="wide">Address line 1<input value={address.line1} onChange={(event) => setAddress({ ...address, line1: event.target.value })} autoComplete="address-line1" /></label>
              <label className="wide">Address line 2 <span>(optional)</span><input value={address.line2} onChange={(event) => setAddress({ ...address, line2: event.target.value })} autoComplete="address-line2" /></label>
              <label>City<input value={address.city} onChange={(event) => setAddress({ ...address, city: event.target.value })} autoComplete="address-level2" /></label>
              <label>State / Province / Region <span>(required)</span><input value={address.state} minLength={2} onChange={(event) => setAddress({ ...address, state: event.target.value })} autoComplete="address-level1" /></label>
              <label>Postal code<input value={address.postal_code} minLength={3} onChange={(event) => setAddress({ ...address, postal_code: event.target.value })} autoComplete="postal-code" /></label>
            </div>

            {shippingError && <div className="fulfillment-inline-error"><TriangleAlert size={14} /><span>{shippingError}</span></div>}
            <button className="secondary fulfillment-shipping-button" disabled={Boolean(busy)} onClick={getShipping}>{busy === 'shipping' ? 'Calculating delivery…' : 'Calculate Shipping'}</button>

            {Boolean(shippingResult) && <>
              <div className="fulfillment-shipping-list">
                {shippingOptions.length ? shippingOptions.map((option, index) => {
                  const label = findString(option, ['service_name', 'service', 'shipping_service', 'name', 'carrier']) || `Shipping option ${index + 1}`
                  const id = findString(option, ['service_id', 'shipping_service_id', 'rate_id', 'id']) || label
                  const price = findNumber(option, ['price_usd', 'cost_usd', 'rate_usd', 'price', 'cost', 'amount'])
                  const eta = findString(option, ['delivery_estimate', 'estimated_delivery', 'transit_time', 'eta', 'days'])
                  return <button type="button" className={`fulfillment-shipping-option ${selectedShipping === id ? 'selected' : ''}`} key={`${id}-${index}`} onClick={() => setSelectedShipping(id)}>
                    <span><b>{label}</b>{eta && <small>{eta}</small>}</span><strong>{formatUsd(price)}</strong>
                  </button>
                }) : <div className="fulfillment-shipping-empty">The provider returned a shipping response, but no carrier rows could be parsed. Please retry or check the destination.</div>}
              </div>
              {shippingOptions.length > 0 && <div className="fulfillment-checkout-note"><ShieldCheck size={14} /><span><strong>Quote complete.</strong> A production checkout link would be the next Slant 3D step. Payment/order submission is intentionally disabled in this hackathon demo.</span></div>}
            </>}
          </> : <div className="fulfillment-warning">The provider returned pricing without a usable quote ID, so quantity and delivery cannot continue.</div>}
        </div>}

        <div className="manufacturing-safety"><ShieldCheck size={14} /><span>{capabilities?.safety || 'Automatic machine execution is disabled by default.'}</span></div>
      </section>
    </div>
  )
}
