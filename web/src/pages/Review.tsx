import { useCallback, useEffect, useState } from 'react'
import { api, type FitScore, type Job } from '../api'
import { ago, Avatar, ErrorBox, gapLabel, Kbd, Spinner, Sticker, useHotkeys, useToast } from '../ui'

const PARTS: [keyof FitScore, string][] = [
  ['role_fit', 'role'], ['skills', 'skills'], ['eligibility', 'eligibility'], ['level', 'level'], ['preferences', 'prefs'],
]
type Out = '' | 'out-left' | 'out-right' | 'out-down'
type Undo = { job: Job; kind: 'skip' | 'later'; index: number } | null

/** Focus review: one good-fit role at a time. Skip archives it, Later sends it to the
 *  back of the stack, Draft queues a package in the background and moves on. */
export default function Review({ onChange, onExit }: { onChange: () => void; onExit: () => void }) {
  const [queue, setQueue] = useState<Job[] | null>(null)
  const [i, setI] = useState(0)
  const [out, setOut] = useState<Out>('')
  const [stats, setStats] = useState({ skipped: 0, drafted: 0, later: 0 })
  const [undo, setUndo] = useState<Undo>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, showToast] = useToast()

  useEffect(() => {
    api.jobs(['review']).then(js => setQueue(js.filter(j => !j.package)))
  }, [])

  const job = queue?.[i]
  const total = queue?.length ?? 0
  const decided = stats.skipped + stats.drafted

  const advance = useCallback((cls: Out, then: () => void) => {
    setOut(cls)
    window.setTimeout(() => { then(); setOut('') }, 150)
  }, [])

  const skip = () => {
    if (!job || out) return
    advance('out-left', () => {
      void api.patchJob(job.id, { status: 'archived' }).then(onChange)
      setUndo({ job, kind: 'skip', index: i })
      setStats(s => ({ ...s, skipped: s.skipped + 1 }))
      setI(n => n + 1)
    })
  }
  const later = () => {
    if (!job || out || !queue) return
    advance('out-down', () => {
      const rest = [...queue.slice(0, i), ...queue.slice(i + 1), job]
      setQueue(rest)
      setUndo({ job, kind: 'later', index: i })
      setStats(s => ({ ...s, later: s.later + 1 }))
    })
  }
  const draft = () => {
    if (!job || out) return
    advance('out-right', () => {
      api.draftLater(job.id).then(() => { onChange(); showToast(`Drafting ${job.company} in the background`) })
        .catch(e => setError((e as Error).message))
      setUndo(null)  // drafting starts right away; no undo
      setStats(s => ({ ...s, drafted: s.drafted + 1 }))
      setI(n => n + 1)
    })
  }
  const doUndo = () => {
    if (!undo || !queue) return
    if (undo.kind === 'skip') {
      void api.patchJob(undo.job.id, { status: 'review' }).then(onChange)
      setStats(s => ({ ...s, skipped: s.skipped - 1 }))
      setI(undo.index)
    } else {
      const without = queue.filter(j => j.id !== undo.job.id)
      setQueue([...without.slice(0, undo.index), undo.job, ...without.slice(undo.index)])
      setStats(s => ({ ...s, later: s.later - 1 }))
    }
    setUndo(null)
    showToast('Undone')
  }
  const open = () => {
    const link = job?.resolved_url || job?.url
    if (link) window.open(link, '_blank', 'noreferrer')
  }

  useHotkeys({
    ArrowLeft: skip, s: skip, ArrowDown: later, l: later, ArrowRight: draft, d: draft, o: open, z: doUndo, Escape: onExit,
  })

  if (!queue) return <div className="review"><Spinner /></div>

  return (
    <div className="review">
      <div className="review-top">
        <button className="btn small ghost" onClick={onExit}>← Inbox <Kbd>esc</Kbd></button>
        <div className="review-progress" aria-label={`${decided} of ${total} decided`}>
          <div style={{ width: `${total ? (100 * Math.min(i, total)) / total : 100}%` }} />
        </div>
        <span className="mono small">{Math.min(i + 1, total)}/{total}</span>
      </div>
      <ErrorBox error={error} />

      {job ? (
        <>
          <div className={`review-card ${out}`} key={job.id}>
            <Sticker score={job.score} size="xl" />
            <div className="row" style={{ gap: 12 }}>
              <Avatar name={job.company} size="lg" />
              <div>
                <div style={{ fontWeight: 700, fontSize: 16 }}>{job.company}</div>
                <div className="muted small">{[job.location, ago(job.age_days), job.source].filter(Boolean).join(' · ')}</div>
              </div>
            </div>
            <h1>{job.title}</h1>
            {job.score_detail && (
              <>
                <p className="summary" style={{ marginTop: 14 }}>{job.score_detail.summary}</p>
                <div className="row" style={{ marginTop: 16, gap: 6 }}>
                  {PARTS.map(([k, label]) => {
                    const v = (job.score_detail![k] as { score: number }).score
                    return <span key={k} className={`chip ${v >= 75 ? 'mint' : v >= 55 ? 'yellow' : 'red'}`}>{label} {v}</span>
                  })}
                  {job.score_detail.missing.slice(0, 3).map(m => <span key={m} className="chip orange" title={m}>{gapLabel(m, 40)}</span>)}
                </div>
                <details className="fold" style={{ marginTop: 18, boxShadow: 'none' }}>
                  <summary>Why this score</summary>
                  <div className="fold-body stack" style={{ gap: 6 }}>
                    {PARTS.map(([k, label]) => (
                      <div key={k} className="small"><b>{label}:</b> {(job.score_detail![k] as { reason: string }).reason}</div>
                    ))}
                  </div>
                </details>
              </>
            )}
            {job.last_error && <div className="note small" style={{ marginTop: 14 }}>Last draft failed: {job.last_error}</div>}
          </div>
          <div className="review-actions">
            <button className="btn" onClick={skip}><Kbd>←</Kbd> Skip</button>
            <button className="btn" onClick={later}><Kbd>↓</Kbd> Later</button>
            <button className="btn" onClick={open}><Kbd>o</Kbd> Posting ↗</button>
            <button className="btn pop" onClick={draft}><Kbd>→</Kbd> Draft package</button>
          </div>
          <p className="small muted" style={{ marginTop: 14 }}>
            {undo ? <>Pressed the wrong one? <Kbd>z</Kbd> to undo.</> : 'Drafts run in the background while you keep going.'}
          </p>
        </>
      ) : (
        <div className="review-card review-done">
          <Sticker score={null} size="xl" />
          <h1 style={{ padding: 0 }}>{total ? 'Stack cleared' : 'Nothing to review'}</h1>
          <p className="muted" style={{ margin: '10px 0 18px' }}>
            {total
              ? `${stats.drafted} drafting, ${stats.skipped} skipped. Drafted packages land in Ready when they finish.`
              : 'New good fits show up here after a scan. Run "Find & score" from the sidebar.'}
          </p>
          <button className="btn pop" onClick={onExit}>Back to inbox</button>
        </div>
      )}
      {toast}
    </div>
  )
}
