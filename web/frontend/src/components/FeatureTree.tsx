import { ChevronDown, ChevronRight, CircleDot, Component, Crosshair, Layers3, Minus, PackageOpen, Ruler, Shapes, SlidersHorizontal } from 'lucide-react'
import { ReactNode, useState } from 'react'
import type { CadState, Selection } from '../types'

function Group({ label, count, children, initial = true }: { label: string; count?: number; children: ReactNode; initial?: boolean }) {
  const [open, setOpen] = useState(initial)
  return <div className="tree-group"><button className="tree-group-title" onClick={() => setOpen(!open)}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<span>{label}</span>{count !== undefined && <small>{count}</small>}</button>{open && <div className="tree-children">{children}</div>}</div>
}

type Props = {
  state: CadState | null
  tab: 'model' | 'parameters'
  onTab: (tab: 'model' | 'parameters') => void
  onSelect: (selection: Selection) => void
  onActivate: (documentId: string) => void
}

export default function FeatureTree({ state, tab, onTab, onSelect, onActivate }: Props) {
  if (!state) return <aside className="model-panel"><div className="panel-loading" /></aside>
  const selected = state.selection
  return (
    <aside className="model-panel">
      <div className="panel-tabs"><button className={tab === 'model' ? 'active' : ''} onClick={() => onTab('model')}>Model</button><button className={tab === 'parameters' ? 'active' : ''} onClick={() => onTab('parameters')}>Parameters</button></div>
      <div className="document-switcher">
        <label>OPEN DOCUMENT</label>
        <select value={state.open_documents.find((item) => item.active)?.id || ''} onChange={(event) => onActivate(event.target.value)}>
          {state.open_documents.map((item) => <option key={item.id} value={item.id}>{item.title}{item.dirty ? ' *' : ''} · {item.type.toUpperCase()}</option>)}
        </select>
      </div>
      {tab === 'parameters' ? (
        <div className="parameter-list">
          <div className="parameter-heading"><SlidersHorizontal size={15} /> User Parameters</div>
          {Object.entries(state.parameters).length === 0 && <div className="panel-empty">No user parameters yet.</div>}
          {Object.entries(state.parameters).map(([name, value]) => <div className="parameter-row" key={name}><strong>{name}</strong><span>{value.expression}</span><small>{value.unit}</small></div>)}
        </div>
      ) : (
        <div className="feature-tree">
          <div className="active-document"><span className={`doc-icon ${state.document?.type || 'part'}`}>{state.document?.type === 'assembly' ? <Layers3 size={17} /> : <PackageOpen size={17} />}</span><div><strong>{state.document?.title || 'No open document'}</strong><span>{state.document?.type?.toUpperCase() || 'NONE'} · {state.document?.units || 'mm'}</span></div></div>
          {state.document?.type === 'part' ? <>
            <Group label="Origin" count={7}>
              {['XY Plane', 'XZ Plane', 'YZ Plane'].map((name) => <div className="tree-item origin" key={name}><Shapes size={14} />{name}</div>)}
              {['X Axis', 'Y Axis', 'Z Axis'].map((name) => <div className="tree-item origin" key={name}><Minus size={14} />{name}</div>)}
              <div className="tree-item origin"><CircleDot size={14} />Center Point</div>
            </Group>
            <Group label="Sketches" count={state.sketches.length}>
              {state.sketches.length === 0 && <div className="tree-empty">No sketches</div>}
              {state.sketches.map((sketch) => <button className="tree-item" key={sketch.name}><Ruler size={14} /><span>{sketch.name}</span><small>{sketch.plane}</small></button>)}
            </Group>
            <Group label="Features" count={state.features.length}>
              {state.features.length === 0 && <div className="tree-empty">No features</div>}
              {state.features.map((feature) => <button className={`tree-item ${selected.type === 'feature' && selected.feature_name === feature.name ? 'selected' : ''}`} key={feature.name} onClick={() => onSelect({ type: 'feature', feature_name: feature.name })}><Component size={14} /><span>{feature.name}</span><small>{feature.kind}</small></button>)}
            </Group>
            <Group label="Work Features" count={(state.work_features?.planes.length || 0) + (state.work_features?.axes.length || 0) + (state.work_features?.points.length || 0)} initial={false}>
              {[...(state.work_features?.planes || []), ...(state.work_features?.axes || []), ...(state.work_features?.points || [])].map((item, index) => <div className="tree-item" key={index}><Crosshair size={14} />{String(item.name || `Work feature ${index + 1}`)}</div>)}
            </Group>
          </> : <>
            <Group label="Occurrences" count={state.occurrences.length}>
              {state.occurrences.length === 0 && <div className="tree-empty">No occurrences</div>}
              {state.occurrences.map((item) => <button className={`tree-item ${selected.type === 'occurrence' && selected.occurrence_name === item.name ? 'selected' : ''}`} key={item.name} onClick={() => onSelect({ type: 'occurrence', occurrence_name: item.name })}><PackageOpen size={14} /><span>{item.name}</span><small>{item.reference_status !== 'resolved' ? 'RELINK' : item.grounded ? 'fixed' : ''}</small></button>)}
            </Group>
            <Group label="Constraints" count={state.constraints.length}>{state.constraints.map((item, index) => <div className="tree-item" key={index}><Crosshair size={14} />{String(item.name || item.type || `Constraint ${index + 1}`)}</div>)}</Group>
            <Group label="Joints" count={state.joints.length}>{state.joints.map((item, index) => <div className="tree-item" key={index}><Component size={14} />{String(item.name || item.type || `Joint ${index + 1}`)}</div>)}</Group>
          </>}
        </div>
      )}
      <div className="panel-foot">Revision {state.document?.revision ?? 0}<span>{state.document?.dirty ? 'Unsaved' : 'Saved'}</span></div>
    </aside>
  )
}
