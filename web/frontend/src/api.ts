export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

function fieldPath(loc: unknown): string {
  if (!Array.isArray(loc)) return ''
  const parts = loc.filter((item) => typeof item === 'string' || typeof item === 'number').map(String).filter((item) => item !== 'body')
  return parts.join(' → ')
}

function readableDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail)
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const row = item as Record<string, unknown>
        const msg = typeof row.msg === 'string' ? row.msg : typeof row.message === 'string' ? row.message : ''
        const path = fieldPath(row.loc)
        if (msg) return path ? `${path}: ${msg}` : msg
      }
      return ''
    }).filter(Boolean)
    return messages.join(' · ')
  }
  if (detail && typeof detail === 'object') {
    const row = detail as Record<string, unknown>
    if (typeof row.msg === 'string') return row.msg
    if (typeof row.message === 'string') return row.message
    if (row.detail !== undefined) return readableDetail(row.detail)
    try { return JSON.stringify(detail) } catch { return 'The server returned an unreadable error.' }
  }
  return ''
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }
  const response = await fetch(path, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const payload = await response.json()
      const formatted = readableDetail(payload?.detail ?? payload?.message ?? payload)
      if (formatted) message = formatted
    } catch {
      const text = await response.text()
      if (text) message = text
    }
    throw new ApiError(message, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function websocketUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

export function download(path: string): void {
  const anchor = document.createElement('a')
  anchor.href = path
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
