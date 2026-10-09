import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Job, type Status, type TrackerPreview, type TrackerRow } from '../api'
import JobDetail from '../components/JobDetail'
import { Avatar, ErrorBox, Spinner, StatusPill, useToast } from '../ui'

const COLS: [Status, string, string][] = [
  ['drafted', 'Ready to submit', 'yellow'], ['applied', 'Applied', 'lilac'], ['interviewing', 'Interviewing', 'blue'],
  ['offer', 'Offer', 'mint'], ['rejected', 'Rejected', 'sunk'],
]
const ACTION_LABEL: Record<TrackerRow['action'], string> = {
  new: 'Add', update: 'Update', same: 'Already here', keep: 'Keep (further along here)',
}

/** Preview of a tracker file before anything is written: what each row will do. */
function ImportPanel({ preview, onApply, onCancel, busy }: {
  preview: TrackerPreview; onApply: (rows: TrackerRow[]) => void; onCancel: () => void; busy: boolean
}) {
  const todo = preview.rows.filter(r => r.action === 'new' || r.action === 'update')
  const c = preview.counts
  const mapped = Object.entries(preview.mapping).map(([h, f]) => `${h} → ${f}`).join(' · ')
  return (
    <div className="card" style={{ marginBottom: 18 }}>
      <div className="card-head">
        <h2>Import {preview.total} rows</h2>
        <p>{c.new ?? 0} new · {c.update ?? 0} update · {c.same ?? 0} already here · {c.keep ?? 0} kept as is</p>
      </div>
      {mapped && <p className="small muted" style={{ marginBottom: 10 }}>Columns: {mapped}</p>}
      <div className="import-scroll">
        <table className="import-table">
          <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Date</th><th>What happens</th></tr></thead>
          <tbody>
            {preview.rows.map((r, i) => (
              <tr key={i} className={r.action === 'same' || r.action === 'keep' ? 'muted' : ''}>
                <td><b>{r.company}</b></td>
                <td>{r.title}</td>
                <td><StatusPill status={r.status} />{r.raw_status && r.raw_status.toLowerCase() !== r.status
                  ? <span className="small muted"> ({r.raw_status})</span> : null}</td>
                <td className="small muted">{r.applied_at ? new Date(r.applied_at * 1000).toLocaleDateString() : '—'}</td>
                <td className="small">{ACTION_LABEL[r.action]}{r.action === 'update' && r.existing_status ? ` (was ${r.existing_status})` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 14 }}>
        <button className="btn pop" disabled={busy || todo.length === 0} onClick={() => onApply(todo)}>
          {busy ? <Spinner /> : `Apply ${todo.length} row${todo.length === 1 ? '' : 's'}`}</button>
        <button className="btn ghost" disabled={busy} onClick={onCancel}>Cancel</button>
        <span className="small muted">Imported roles join the Tracker and are skipped by every future scan. Nothing is sent anywhere.</span>
      </div>
    </div>
  )
}

export default function Board({ tick, onChange }: { tick: number; onChange: () => void }) {
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [over, setOver] = useState<Status | null>(null)
  const [preview, setPreview] = useState<TrackerPreview | null>(null)
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [toast, showToast] = useToast()
  const load = useCallback(async () => setJobs(await api.jobs(COLS.map(c => c[0]))), [])
  useEffect(() => { void load() }, [load, tick])

  const pickFile = async (f: File | undefined) => {
    if (!f) return
    setImportBusy(true); setImportError(null); setPreview(null)
    try { setPreview(await api.importTracker(f)) } catch (e) { setImportError((e as Error).message) } finally {
      setImportBusy(false); if (fileRef.current) fileRef.current.value = ''
    }
  }
  const exportTracker = async (format: 'csv' | 'md') => {
    try {
      const text = await api.exportTracker(format)
      const a = document.createElement('a')
      a.href = URL.createObjectURL(new Blob([text], { type: format === 'md' ? 'text/markdown' : 'text/csv' }))
      a.download = `applications.${format}`; a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) { setImportError((e as Error).message) }
  }
  const applyImport = async (rows: TrackerRow[]) => {
    setImportBusy(true); setImportError(null)
    try {
      const r = await api.applyTrackerImport(rows)
      showToast(`Imported: ${r.added} added, ${r.updated} updated`)
      setPreview(null); void load(); onChange()
    } catch (e) { setImportError((e as Error).message) } finally { setImportBusy(false) }
  }

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
        <p>Drag a card when something changes. Only you move things to Applied.</p></div>
        <div className="row">
          <input ref={fileRef} type="file" accept=".csv,.tsv,.txt,.md" style={{ display: 'none' }}
            onChange={e => void pickFile(e.target.files?.[0])} />
          <button className="btn" disabled={importBusy} onClick={() => fileRef.current?.click()}
            title="Bring in a spreadsheet of roles you've already applied to, so the scanner never shows them again">
            {importBusy && !preview ? <Spinner /> : 'Import tracker'}</button>
          <button className="btn" onClick={() => void exportTracker('csv')} title="Download your applications as a spreadsheet">Export CSV</button>
          <button className="btn" onClick={() => void exportTracker('md')} title="Download your applications as a Markdown table">Export MD</button>
        </div>
      </div>
      <ErrorBox error={importError} />
      {preview && <ImportPanel preview={preview} busy={importBusy} onApply={applyImport} onCancel={() => setPreview(null)} />}
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
