import { useEffect, useState } from 'react'
import { api, type FitScore, type Job, type Profile, type Status, type Tailored } from '../api'
import { ago, copy, ErrorBox, ResumePreview, ScoreNum, Spinner, StatusPill } from '../ui'

const PARTS: [keyof FitScore, string][] = [
  ['role_fit', 'Role fit'], ['skills', 'Skills'], ['eligibility', 'Eligibility'], ['level', 'Level'], ['preferences', 'Preferences'],
]
const TRACK: Status[] = ['applied', 'interviewing', 'offer', 'rejected']

export default function JobDetail({ id, onChange }: { id: string; onChange: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState<'' | 'score' | 'draft'>('')
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'resume' | 'answers' | 'jd'>('resume')

  useEffect(() => {
    setJob(null); setError(null)
    api.job(id).then(setJob).catch(e => setError((e as Error).message))
  }, [id])

  if (!job) return <div className="detail-inner">{error ? <ErrorBox error={error} /> : <Spinner />}</div>

  const act = async (kind: 'score' | 'draft') => {
    setBusy(kind); setError(null)
    try { setJob(await (kind === 'score' ? api.score(job.id) : api.draft(job.id))); onChange() }
    catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  const setStatus = async (status: Status) => {
    setJob(await api.patchJob(job.id, { status })); onChange()
  }
  const link = job.resolved_url || job.url
  const fit = job.score_detail

  return (
    <div className="detail-inner stack">
      <div>
        <div className="row"><h1 className="grow">{job.title}</h1><StatusPill status={job.status} /></div>
        <div className="row" style={{ marginTop: 4 }}>
          <b>{job.company}</b>
          <span className="muted">{[job.location, job.source, ago(job.age_days)].filter(Boolean).join(' · ')}</span>
        </div>
      </div>

      <div className="row">
        {link && <a className="btn" href={link} target="_blank" rel="noreferrer">Open posting ↗</a>}
        <button className="btn" disabled={!!busy} onClick={() => act('score')}>
          {busy === 'score' ? <><Spinner /> Scoring…</> : fit ? 'Re-score' : 'Score fit'}</button>
        <button className="btn primary" disabled={!!busy || job.status === 'ineligible'} onClick={() => act('draft')}>
          {busy === 'draft' ? <><Spinner /> Drafting (1-2 min)…</> : job.package ? 'Redraft package' : 'Draft package'}</button>
        <span className="grow" />
        {job.status !== 'archived'
          ? <button className="btn" onClick={() => setStatus('archived')}>Archive</button>
          : <button className="btn" onClick={() => setStatus(job.score !== null ? 'scored' : 'new')}>Unarchive</button>}
      </div>
      <ErrorBox error={error} />

      <div className="card row">
        <span className="small muted">Tracker:</span>
        {TRACK.map(s => (
          <button key={s} className={`btn small ${job.status === s ? 'primary' : ''}`} onClick={() => setStatus(s)}>
            {s === 'applied' ? 'I applied' : s[0].toUpperCase() + s.slice(1)}</button>
        ))}
        <span className="small muted">Only you mark applications as submitted.</span>
      </div>

      {fit && (
        <div className="card">
          <div className="row" style={{ marginBottom: 10 }}>
            <h2 className="grow" style={{ margin: 0 }}>Fit</h2><ScoreNum score={job.score} />
          </div>
          {fit.ineligible && <div className="error" style={{ marginBottom: 10 }}>Ineligible: {fit.gate}</div>}
          <p style={{ marginBottom: 12 }}>{fit.summary}</p>
          <div className="bars">
            {PARTS.map(([k, label]) => {
              const s = fit[k] as { score: number; reason: string }
              return (
                <div key={k} style={{ display: 'contents' }}>
                  <span className="small">{label}</span>
                  <div className="bar"><div style={{ width: `${s.score}%` }} /></div>
                  <span className="small" style={{ textAlign: 'right' }}>{s.score}</span>
                  <div className="reason">{s.reason}</div>
                </div>
              )
            })}
          </div>
          {fit.missing.length > 0 && (
            <p className="small" style={{ marginTop: 8 }}><b>Missing:</b> {fit.missing.join(' · ')}</p>
          )}
        </div>
      )}

      <div className="tabs">
        <button className={tab === 'resume' ? 'on' : ''} onClick={() => setTab('resume')}>Tailored resume</button>
        <button className={tab === 'answers' ? 'on' : ''} onClick={() => setTab('answers')}>Answers</button>
        <button className={tab === 'jd' ? 'on' : ''} onClick={() => setTab('jd')}>Job description</button>
      </div>

      {tab === 'jd' && (job.description
        ? <div className="jd">{job.description}</div>
        : <p className="muted">Not fetched yet. It's fetched when the role is scored.</p>)}
      {tab !== 'jd' && !job.package && (
        <p className="muted">No package yet. "Draft package" tailors a one-page resume from your experience bank and
          drafts answers to this posting's questions. Nothing is submitted.</p>
      )}
      {tab === 'resume' && job.package && <ResumeTab job={job} onSaved={setJob} />}
      {tab === 'answers' && job.package && <AnswersTab job={job} />}

      <Notes job={job} />
    </div>
  )
}

function ResumeTab({ job, onSaved }: { job: Job; onSaved: (j: Job) => void }) {
  const pkg = job.package!
  const [t, setT] = useState<Tailored>(pkg.tailored)
  const [bank, setBank] = useState<Profile | null>(null)
  const [editing, setEditing] = useState(false)
  const [bust, setBust] = useState(pkg.created)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setT(pkg.tailored) }, [pkg])
  useEffect(() => { if (editing && !bank) api.profile().then(setBank) }, [editing, bank])
  const title = (entryId: string) => bank?.experience.find(e => e.id === entryId)?.title ?? entryId

  const save = async () => {
    setBusy(true)
    try { const j = await api.saveTailored(job.id, t); onSaved(j); setBust(Date.now()); setEditing(false) } finally { setBusy(false) }
  }
  return (
    <div className="stack">
      <div className="row">
        {pkg.pages > 1 && <span className="pill warn">{pkg.pages} pages. Trim some bullets.</span>}
        <span className="grow" />
        <button className="btn small" onClick={() => setEditing(e => !e)}>{editing ? 'Cancel edits' : 'Edit bullets'}</button>
        <a className="btn small" href={api.pdfUrl(job.id, bust)} target="_blank" rel="noreferrer">Open PDF</a>
      </div>
      {pkg.tailored.notes.length > 0 && (
        <div className="note small"><b>Notes from the tailor:</b>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{pkg.tailored.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
      )}
      {editing ? (
        <div className="card stack">
          <label className="field">Headline<input value={t.headline} onChange={e => setT({ ...t, headline: e.target.value })} /></label>
          {t.entries.map((en, i) => (
            <div key={en.entry_id}>
              <b>{title(en.entry_id)}</b>
              {en.bullets.map((b, k) => (
                <div className="bullet-edit" key={k} style={{ marginTop: 6 }}>
                  <textarea rows={2} style={{ minHeight: 0 }} value={b} onChange={e => {
                    const entries = [...t.entries]
                    const bullets = [...en.bullets]; bullets[k] = e.target.value
                    entries[i] = { ...en, bullets }; setT({ ...t, entries })
                  }} />
                  <button className="btn small" aria-label="Remove bullet" onClick={() => {
                    const entries = [...t.entries]
                    entries[i] = { ...en, bullets: en.bullets.filter((_, x) => x !== k), source_bullet_ids: en.source_bullet_ids.filter((_, x) => x !== k) }
                    setT({ ...t, entries: entries.filter(e => e.bullets.length) })
                  }}>×</button>
                </div>
              ))}
            </div>
          ))}
          <div><button className="btn primary" disabled={busy} onClick={save}>{busy ? <Spinner /> : 'Re-render PDF'}</button></div>
        </div>
      ) : (
        <ResumePreview pages={pkg.pages} src={page => api.pngUrl(job.id, page, bust)} />
      )}
    </div>
  )
}

function AnswersTab({ job }: { job: Job }) {
  const pkg = job.package!
  const [answers, setAnswers] = useState(pkg.answers.answers.map(a => a.answer))
  const [savedQ, setSavedQ] = useState<Set<number>>(new Set())
  useEffect(() => { setAnswers(pkg.answers.answers.map(a => a.answer)) }, [pkg])
  return (
    <div className="stack">
      <p className="small muted">{pkg.questions_source === 'ats'
        ? "These are this posting's real application questions, pulled from the ATS."
        : "This ATS doesn't publish its questions, so these are the usual ones. Check the form for others."}</p>
      {pkg.answers.gaps.length > 0 && (
        <div className="note small"><b>Only you can answer:</b>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{pkg.answers.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul></div>
      )}
      <div className="card">
        {pkg.answers.answers.map((a, i) => (
          <div className="answer" key={i}>
            <div className="row q"><span className="grow">{a.question}</span>
              {a.source === 'learned' && <span className="pill good">saved answer</span>}</div>
            <textarea rows={Math.min(8, Math.max(2, Math.ceil(answers[i].length / 90)))} value={answers[i]}
              onChange={e => { const n = [...answers]; n[i] = e.target.value; setAnswers(n) }} />
            <div className="row" style={{ marginTop: 6 }}>
              <button className="btn small" onClick={() => copy(answers[i])}>Copy</button>
              {a.source !== 'learned' && (
                <button className="btn small" disabled={savedQ.has(i)} onClick={async () => {
                  await api.learn({ question: a.question, answer: answers[i] })
                  setSavedQ(new Set(savedQ).add(i))
                }}>{savedQ.has(i) ? 'Saved for reuse' : 'Save for reuse'}</button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Notes({ job }: { job: Job }) {
  const [notes, setNotes] = useState(job.notes)
  useEffect(() => setNotes(job.notes), [job.id, job.notes])
  return (
    <label className="field">Notes
      <textarea value={notes} placeholder="Referral, recruiter name, OA deadline…" onChange={e => setNotes(e.target.value)}
        onBlur={() => notes !== job.notes && api.patchJob(job.id, { notes })} />
    </label>
  )
}
