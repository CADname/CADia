export type User = { id: string; email: string; display_name: string }

export type Project = {
  id: string
  name: string
  description: string
  created_at: string
  updated_at: string
}

export type DocumentSession = {
  id: string
  title: string
  path?: string | null
  type: 'part' | 'assembly'
  active: boolean
  dirty: boolean
}

export type SelectionItem = {
  type: 'face' | 'edge'
  face_ref?: string
  edge_ref?: string
  occurrence_name?: string
}

export type Selection = {
  type?: 'face' | 'edge' | 'feature' | 'occurrence' | 'part'
  face_ref?: string
  edge_ref?: string
  face_refs?: string[]
  edge_refs?: string[]
  items?: SelectionItem[]
  count?: number
  feature_name?: string
  occurrence_name?: string
  [key: string]: unknown
}

export type CadState = {
  document: null | {
    title: string
    type: 'part' | 'assembly'
    units: string
    revision: number
    has_geometry: boolean
    dirty: boolean
    view_orientation: string
  }
  parameters: Record<string, { expression: string; unit: string }>
  sketches: Array<{ name: string; plane: string; closed: boolean; entities: unknown[]; dimensions: unknown[]; constraints: unknown[] }>
  sketches3d: Array<Record<string, unknown>>
  work_features?: { planes: Array<Record<string, unknown>>; axes: Array<Record<string, unknown>>; points: Array<Record<string, unknown>> }
  features: Array<{ name: string; kind: string; operation: string; params: Record<string, unknown>; suppressed?: boolean }>
  occurrences: Array<{ name: string; grounded: boolean; suppressed: boolean; reference_status: string }>
  constraints: Array<Record<string, unknown>>
  joints: Array<Record<string, unknown>>
  selection: Selection
  open_documents: DocumentSession[]
  undo_available: boolean
  redo_available: boolean
}

export type FaceMesh = {
  id: string
  occurrence_name?: string | null
  positions: number[]
  indices: number[]
  topology: Record<string, unknown>
}

export type EdgeMesh = {
  id: string
  occurrence_name?: string | null
  positions: number[]
  topology: Record<string, unknown>
}

export type CadMesh = {
  revision: number
  document_type?: string | null
  faces: FaceMesh[]
  edges: EdgeMesh[]
  bounds: null | { min: [number, number, number]; max: [number, number, number] }
  stats: { faces: number; edges: number; volume_mm3: number }
}

export type ChatMessage = { id?: string; role: 'user' | 'assistant' | 'error' | 'system'; content: string; created_at?: string }

export type ModelInfo = {
  id: string
  model?: string
  displayName?: string
  isDefault?: boolean
  defaultReasoningEffort?: string
  supportedReasoningEfforts?: Array<{ reasoningEffort: string; description?: string }>
}

export type AccountInfo = {
  provider?: 'codex' | 'copilot' | 'openai' | 'anthropic' | 'gemini' | string
  providerLabel?: string
  connected?: boolean
  model?: string | null
  supportsReasoning?: boolean
  account: null | { type: string; email?: string | null; planType?: string | null }
  requiresOpenaiAuth?: boolean
}
