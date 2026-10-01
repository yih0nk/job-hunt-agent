import { useCallback, useEffect, useState } from 'react'
import { api, type Job, type Status } from '../api'
import JobDetail from '../components/JobDetail'
import { ago, ErrorBox, Field, ScoreNum, Spinner, StatusPill } from '../ui'

const TABS: [string, Status[]][] = [
  ['Good fit', ['review']],
  ['Ready to review', ['drafted']],
  ['Unscored', ['new']],
  ['Low fit', ['scored']],
  ['Ineligible', ['ineligible']],
  ['Archived', ['archived']],
]

export default function Inbox({ tick, onChange }: { tick: number; onChange: () => void }) {
  const [tab, setTab] = useState(0)
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)

  const load = useCallback(async () => {
    const js = await api.jobs(TABS[tab][1])
    setJobs(js)
    setSel(s => (s && js.some(j => j.id === s)) ? s : js[0]?.id ?? null)
  }, [tab])
  useEffect(() => { void load() }, [load, tick])

  const shown = (jobs ?? []).filter(j => !q || `${j.company} ${j.title} ${j.location}`.toLowerCase().includes(q.toLowerCase()))

  return (
    <div className="split">
      <div className="list">
        <div className="list-head">
          <div className="row">
            <input className="grow" style={{ width: 'auto' }} placeholder="Filter…" value={q} onChange={e => setQ(e.target.value)} />
            <button className="btn" onClick={() => setAdding(true)}>+ Add role</button>
          </div>
          <div className="tabs">
            {TABS.map(([label], i) => (
              <button key={label} className={i === tab ? 'on' : ''} onClick={() => setTab(i)}>{label}</button>
            ))}
          </div>
        </div>
        {jobs === null && <div className="empty"><Spinner /></div>}
        {jobs !== null && shown.length === 0 && (
          <div className="empty">{tab === 0 ? 'No good-fit roles yet. Run "Find & score new roles".' : 'Nothing here.'}</div>
        )}
        {shown.map(j => (
          <div key={j.id} className={`item ${sel === j.id ? 'on' : ''}`} onClick={() => { setSel(j.id); setAdding(false) }}>
            <ScoreNum score={j.score} />
            <div className="grow">
              <div className="t">{j.company}</div>
              <div>{j.title}</div>
              <div className="s">{[j.location, ago(j.age_days)].filter(Boolean).join(' · ')}</div>
            </div>
            {j.status === 'drafted' && <StatusPill status={j.status} />}
          </div>
        ))}
      </div>
      <div className="detail">
        {adding ? <AddJob onAdded={id => { setAdding(false); setTab(2); setSel(id); onChange() }} onCancel={() => setAdding(false)} />
          : sel ? <JobDetail id={sel} onChange={() => { void load(); onChange() }} />
            : <div className="empty">Select a role</div>}
      </div>
    </div>
  )
}

function AddJob({ onAdded, onCancel }: { onAdded: (id: string) => void; onCancel: () => void }) {
  const [f, setF] = useState({ company: '', title: '', url: '', location: '', description: '' })
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v })
  const add = async () => {
    try { const j = await api.addJob(f); onAdded(j.id) } catch (e) { setError((e as Error).message) }
  }
  return (
    <div className="detail-inner stack">
      <h1>Add a role</h1>
      <p className="muted">Found something outside your sources? Add it here, then score or draft it like any other role.</p>
      <div className="grid2">
        <Field label="Company"><input value={f.company} onChange={e => set('company', e.target.value)} /></Field>
        <Field label="Title"><input value={f.title} onChange={e => set('title', e.target.value)} /></Field>
        <Field label="Posting URL"><input value={f.url} onChange={e => set('url', e.target.value)} /></Field>
        <Field label="Location"><input value={f.location} onChange={e => set('location', e.target.value)} /></Field>
      </div>
      <Field label="Job description (optional)" hint="Paste it if the posting page can't be fetched (e.g. LinkedIn).">
        <textarea rows={8} value={f.description} onChange={e => set('description', e.target.value)} />
      </Field>
      <ErrorBox error={error} />
      <div className="row">
        <button className="btn primary" disabled={!f.company || !f.title} onClick={add}>Add</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
