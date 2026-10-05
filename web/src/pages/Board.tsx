import { useCallback, useEffect, useState } from 'react'
import { api, type Job, type Status } from '../api'
import JobDetail from '../components/JobDetail'
import { Avatar, Spinner, useToast } from '../ui'

const COLS: [Status, string, string][] = [
  ['drafted', 'Ready to submit', 'yellow'], ['applied', 'Applied', 'lilac'], ['interviewing', 'Interviewing', 'blue'],
  ['offer', 'Offer', 'mint'], ['rejected', 'Rejected', 'sunk'],
]

export default function Board({ tick, onChange }: { tick: number; onChange: () => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [over, setOver] = useState<Status | null>(null)
  const [toast, showToast] = useToast()
  const load = useCallback(async () => setJobs(await api.jobs(COLS.map(c => c[0]))), [])
  useEffect(() => { void load() }, [load, tick])

  const drop = async (id: string, status: Status) => {
    setOver(null)
    const j = jobs?.find(x => x.id === id)
    if (!j || j.status === status) return
    setJobs(js => js!.map(x => x.id === id ? { ...x, status } : x))   // optimistic
    await api.patchJob(id, { status })
    showToast(`${j.company} → ${COLS.find(c => c[0] === status)![1]}`)
    void load(); onChange()
  }

  if (open) {
    return (
      <div>
        <div style={{ padding: '18px 30px 0' }}><button className="btn small" onClick={() => { setOpen(null); void load() }}>← Tracker</button></div>
        <JobDetail id={open} onChange={() => { void load(); onChange() }} />
      </div>
    )
  }
  return (
    <div className="page" style={{ maxWidth: 'none' }}>
      <div className="page-head"><div><h1>Tracker</h1>
        <p>Drag a card when something changes. Only you move things to Applied.</p></div></div>
      {!jobs ? <Spinner /> : (
        <div className="board">
          {COLS.map(([status, label, colour]) => {
            const col = jobs.filter(j => j.status === status)
            return (
              <div className="col" key={status}
                style={over === status ? { background: `var(--${colour})`, color: 'var(--on-pop)' } : undefined}
                onDragOver={e => { e.preventDefault(); setOver(status) }}
                onDragLeave={() => setOver(o => (o === status ? null : o))}
                onDrop={e => { e.preventDefault(); void drop(e.dataTransfer.getData('text/plain'), status) }}>
                <h3><span className={`chip ${colour === 'sunk' ? '' : colour}`}>{label}</span><span className="mono">{col.length}</span></h3>
                {col.map(j => (
                  <div className="tile" key={j.id} draggable onClick={() => setOpen(j.id)}
                    onDragStart={e => e.dataTransfer.setData('text/plain', j.id)}>
                    <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
                      <Avatar name={j.company} url={j.resolved_url || j.url} />
                      <div style={{ minWidth: 0 }}>
                        <div className="t">{j.company}</div>
                        <div className="small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{j.title}</div>
                      </div>
                    </div>
                    {j.applied_at && <div className="small muted" style={{ marginTop: 6 }}>Applied {new Date(j.applied_at * 1000).toLocaleDateString()}</div>}
                  </div>
                ))}
                {col.length === 0 && <div className="small muted" style={{ padding: '6px 2px' }}>Drop here</div>}
              </div>
            )
          })}
        </div>
      )}
      {toast}
    </div>
  )
}
