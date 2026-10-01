import { useCallback, useEffect, useState } from 'react'
import { api, type Job, type Status } from '../api'
import JobDetail from '../components/JobDetail'
import { Spinner } from '../ui'

const COLS: [Status, string][] = [
  ['drafted', 'Ready to submit'], ['applied', 'Applied'], ['interviewing', 'Interviewing'], ['offer', 'Offer'], ['rejected', 'Rejected'],
]

export default function Board({ tick, onChange }: { tick: number; onChange: () => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const load = useCallback(async () => setJobs(await api.jobs(COLS.map(c => c[0]))), [])
  useEffect(() => { void load() }, [load, tick])

  if (open) {
    return (
      <div>
        <div style={{ padding: '16px 26px 0' }}><button className="btn" onClick={() => { setOpen(null); void load() }}>← Tracker</button></div>
        <JobDetail id={open} onChange={() => { void load(); onChange() }} />
      </div>
    )
  }
  return (
    <div className="page" style={{ maxWidth: 'none' }}>
      <div className="page-head"><div><h1>Tracker</h1>
        <p>Packages waiting for you, and everything you've submitted.</p></div></div>
      {!jobs ? <Spinner /> : (
        <div className="board">
          {COLS.map(([status, label]) => {
            const col = jobs.filter(j => j.status === status)
            return (
              <div className="col" key={status}>
                <h3>{label}<span>{col.length}</span></h3>
                {col.map(j => (
                  <div className="tile" key={j.id} onClick={() => setOpen(j.id)}>
                    <div className="t">{j.company}</div>
                    <div className="small">{j.title}</div>
                    {j.applied_at && <div className="small muted">Applied {new Date(j.applied_at * 1000).toLocaleDateString()}</div>}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
