import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type AppState, type Job, type Status } from '../api'
import JobDetail from '../components/JobDetail'
import { Avatar, ErrorBox, Field, JobChips, Kbd, Spinner, Sticker, useHotkeys, useToast } from '../ui'

const TABS: [string, Status[]][] = [
  ['Good fit', ['review']],
  ['Ready', ['drafted', 'drafting']],
  ['Unscored', ['new']],
  ['Low fit', ['scored']],
  ['Ineligible', ['ineligible']],
  ['Archived', ['archived']],
]

export default function Inbox({ tick, counts, onChange, onReview, startTab = 0 }: {
  tick: number; counts: AppState['counts']; onChange: () => void; onReview: () => void; startTab?: number
}) {
  const [tab, setTab] = useState(startTab)
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)
  const [toast, showToast] = useToast()
  const filterRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => setTab(startTab), [startTab])

  const load = useCallback(async () => {
    const js = await api.jobs(TABS[tab][1])
    setJobs(js)
    setSel(s => (s && js.some(j => j.id === s)) ? s : js[0]?.id ?? null)
  }, [tab])
  useEffect(() => { void load() }, [load, tick])
  // Keep "drafting" rows fresh while something is drafting.
  useEffect(() => {
    if (!jobs?.some(j => j.status === 'drafting')) return
    const t = window.setInterval(load, 4000)
    return () => window.clearInterval(t)
  }, [jobs, load])

  const shown = (jobs ?? []).filter(j => !q || `${j.company} ${j.title} ${j.location}`.toLowerCase().includes(q.toLowerCase()))
  const idx = shown.findIndex(j => j.id === sel)
  const current = idx >= 0 ? shown[idx] : null

  const move = (d: number) => {
    if (!shown.length) return
    const n = Math.max(0, Math.min(shown.length - 1, (idx < 0 ? 0 : idx) + d))
    setSel(shown[n].id); setAdding(false)
    listRef.current?.querySelectorAll('.item')[n]?.scrollIntoView({ block: 'nearest' })
  }
  const archive = async () => {
    if (!current) return
    await api.patchJob(current.id, { status: 'archived' })
    showToast(`Archived ${current.company}`)
    move(1); await load(); onChange()
  }
  const draft = async () => {
    if (!current || current.status === 'drafting') return
    await api.draftLater(current.id)
    showToast(`Drafting ${current.company} in the background`)
    await load(); onChange()
  }
  const open = () => {
    const link = current?.resolved_url || current?.url
    if (link) window.open(link, '_blank', 'noreferrer')
  }

  useHotkeys({
    j: () => move(1), ArrowDown: () => move(1), k: () => move(-1), ArrowUp: () => move(-1),
    a: archive, d: draft, o: open, r: onReview, '/': () => filterRef.current?.focus(),
  }, !adding)

  const reviewCount = counts.review ?? 0
  return (
    <div className="split">
      <div className="list" ref={listRef}>
        <div className="list-head">
          <div className="row">
            <input ref={filterRef} className="grow" style={{ width: 'auto' }} placeholder="Filter roles   /" value={q}
              onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Escape' && (e.currentTarget.blur())} />
            <button className="btn small" onClick={() => setAdding(true)}>+ Add</button>
          </div>
          {reviewCount > 0 && (
            <button className="btn pop" style={{ width: '100%', justifyContent: 'center', marginTop: 12 }} onClick={onReview}>
              Review {reviewCount} good fit{reviewCount === 1 ? '' : 's'} one by one <Kbd>r</Kbd></button>
          )}
          <div className="tabs">
            {TABS.map(([label, sts], i) => {
              const n = sts.reduce((acc, s) => acc + (counts[s] ?? 0), 0)
              return <button key={label} className={i === tab ? 'on' : ''} onClick={() => setTab(i)}>
                {label}{n > 0 && <span className="n">{n}</span>}</button>
            })}
          </div>
        </div>
        {jobs === null && <div className="empty"><Spinner /></div>}
        {jobs !== null && shown.length === 0 && (
          <div className="empty">{q ? 'No roles match that filter.'
            : tab === 0 ? 'No good fits waiting. Run "Find & score" in the sidebar.'
              : tab === 1 ? 'No packages yet. Press d on a role to draft one.' : 'Nothing here.'}</div>
        )}
        {shown.map(j => (
          <div key={j.id} className={`item ${sel === j.id && !adding ? 'on' : ''}`}
            onClick={() => { setSel(j.id); setAdding(false) }}>
            <Avatar name={j.company} url={j.resolved_url || j.url} />
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="t">{j.company}</div>
              <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{j.title}</div>
              <JobChips job={j} />
            </div>
            <Sticker score={j.score} ineligible={j.status === 'ineligible'} />
          </div>
        ))}
        {shown.length > 0 && (
          <div className="small muted" style={{ padding: 14, lineHeight: 2 }}>
            <Kbd>j</Kbd> <Kbd>k</Kbd> move · <Kbd>d</Kbd> draft · <Kbd>a</Kbd> archive · <Kbd>o</Kbd> posting · <Kbd>r</Kbd> review
          </div>
        )}
      </div>
      <div className="detail">
        {adding ? <AddJob onAdded={id => { setAdding(false); setTab(2); setSel(id); onChange() }} onCancel={() => setAdding(false)} />
          : sel ? <JobDetail id={sel} tick={tick} onChange={() => { void load(); onChange() }} />
            : <div className="empty">Pick a role on the left.</div>}
      </div>
      {toast}
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
      <Field label="Job description (optional)" hint="Paste it if the posting page can't be fetched, like LinkedIn.">
        <textarea rows={8} value={f.description} onChange={e => set('description', e.target.value)} />
      </Field>
      <ErrorBox error={error} />
      <div className="row">
        <button className="btn primary" disabled={!f.company || !f.title} onClick={add}>Add role</button>
        <button className="btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
