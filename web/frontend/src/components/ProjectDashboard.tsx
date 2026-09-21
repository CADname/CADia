import { FormEvent, useEffect, useState } from 'react'
import { Box, CalendarDays, ChevronRight, FolderKanban, LogOut, Plus, Search, Sparkles } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { Project, User } from '../types'

type Props = { user: User; onLogout: () => void }

export default function ProjectDashboard({ user, onLogout }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [query, setQuery] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    api<Project[]>('/api/projects').then(setProjects).catch((reason) => setError(String(reason)))
  }, [])

  async function createProject(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      const project = await api<Project>('/api/projects', { method: 'POST', body: JSON.stringify({ name, description }) })
      setProjects((current) => [project, ...current])
      setShowCreate(false)
      setName('')
      setDescription('')
      navigate(`/projects/${project.id}`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  const filtered = projects.filter((project) => `${project.name} ${project.description}`.toLowerCase().includes(query.toLowerCase()))

  return (
    <main className="dashboard-page">
      <header className="dashboard-header">
        <div className="brand-lockup"><span className="brand-mark">C</span><span>CAD<b>ia</b></span></div>
        <div className="header-actions"><span className="user-chip">{user.display_name.slice(0, 1)}</span><span className="user-name">{user.display_name}</span><button className="icon-button" title="Sign out" onClick={onLogout}><LogOut size={18} /></button></div>
      </header>
      <section className="dashboard-content">
        <div className="dashboard-intro">
          <div><p className="eyebrow">YOUR WORKSPACE</p><h1>What will you design?</h1><p>Each project keeps CAD documents, feature history, AI conversations, and Codex state isolated.</p></div>
          <button className="primary" onClick={() => setShowCreate(true)}><Plus size={17} /> New Project</button>
        </div>
        <div className="dashboard-toolbar">
          <div className="search-box"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search projects" /></div>
          <span>{filtered.length} projects</span>
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="project-grid">
          <button className="project-card create-card" onClick={() => setShowCreate(true)}><span className="create-icon"><Plus /></span><strong>New CAD Project</strong><small>Start from an empty parametric part</small></button>
          {filtered.map((project, index) => (
            <button className="project-card" key={project.id} onClick={() => navigate(`/projects/${project.id}`)}>
              <div className={`project-visual shade-${index % 4}`}><Box size={64} strokeWidth={0.75} /><Sparkles size={17} /></div>
              <div className="project-card-body">
                <div className="project-title"><FolderKanban size={16} /><strong>{project.name}</strong><ChevronRight size={16} /></div>
                <p>{project.description || 'No description'}</p>
                <span><CalendarDays size={13} /> {new Date(project.updated_at).toLocaleDateString('en-US')}</span>
              </div>
            </button>
          ))}
        </div>
      </section>
      {showCreate && (
        <div className="modal-backdrop" onMouseDown={() => setShowCreate(false)}>
          <form className="modal-card create-modal" onSubmit={createProject} onMouseDown={(event) => event.stopPropagation()}>
            <p className="eyebrow">NEW PROJECT</p><h2>New CAD Project</h2>
            <label>Project name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Example: Gearbox concept" required maxLength={120} /></label>
            <label>Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Optional" rows={3} /></label>
            <div className="modal-actions"><button type="button" className="secondary" onClick={() => setShowCreate(false)}>Cancel</button><button className="primary">Create Project</button></div>
          </form>
        </div>
      )}
    </main>
  )
}
