import { useCallback, useEffect, useState } from 'react'
import { api, type FitScore, type Job, type Profile, type Status, type Tailored } from '../api'
import { ago, Avatar, copy, ErrorBox, money, ResumePreview, Spinner, StatusPill, Sticker, useToast } from '../ui'

const PARTS: [keyof FitScore, string][] = [
  ['role_fit', 'Role fit'], ['skills', 'Skills'], ['eligibility', 'Eligibility'], ['level', 'Level'], ['preferences', 'Preferences'],
]
const CURRENT_LABEL: Partial<Record<Status, string>> = {
  new: 'Unscored', scored: 'Low fit', ineligible: 'Ineligible', drafting: 'Drafting…', drafted: 'Ready to submit',
}
const COST_LABEL: Record<string, string> = { score: 'scoring', tailor: 'resume', answers: 'answers' }
// What the user can move a role to. "Applied" and later are only ever set here, by hand.
const MOVES: [Status, string][] = [
  ['review', 'In inbox'], ['applied', 'Applied'], ['interviewing', 'Interviewing'], ['offer', 'Offer'],
  ['rejected', 'Rejected'], ['archived', 'Archived'],
]

export default function JobDetail({ id, tick = 0, onChange }: { id: string; tick?: number; onChange: () => void }) {
  const [job, setJob] = useState<Job | null>(null)
  const [busy, setBusy] = useState<'' | 'score'>('')
  const [error, setError] = useState<string | null>(null)
  const [toast, showToast] = useToast()

  const reload = useCallback(() => api.job(id).then(setJob).catch(e => setError((e as Error).message)), [id])
  useEffect(() => { setJob(null); setError(null); void reload() }, [id, reload])
  useEffect(() => { if (tick) void reload() }, [tick, reload])
  // Poll while a background draft is running.
  useEffect(() => {
    if (job?.status !== 'drafting') return
    const t = window.setInterval(async () => {
      const j = await api.job(id)
      setJob(j)
      if (j.status !== 'drafting') { onChange(); if (j.package) showToast('Package ready') }
    }, 3000)
    return () => window.clearInterval(t)
  }, [job?.status, id, onChange, showToast])

  if (!job) return <div className="detail-inner">{error ? <ErrorBox error={error} /> : <Spinner />}</div>

  const score = async () => {
    setBusy('score'); setError(null)
    try { setJob(await api.score(job.id)); onChange() } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  const draft = async () => {
    setError(null)
    try { setJob(await api.draftLater(job.id)); onChange() } catch (e) { setError((e as Error).message) }
  }
  const setStatus = async (status: Status) => {
    setJob(await api.patchJob(job.id, { status })); onChange()
    showToast(status === 'applied' ? 'Marked applied. Nice.' : 'Moved')
  }
  const link = job.resolved_url || job.url
  const fit = job.score_detail
  const drafting = job.status === 'drafting'
  const statusOptions = MOVES.some(([s]) => s === job.status) ? MOVES
    : [[job.status, CURRENT_LABEL[job.status] ?? job.status] as [Status, string], ...MOVES]

  return (
    <div className="detail-inner stack" style={{ gap: 18 }}>
      <div className="hero">
        <Avatar name={job.company} size="lg" />
        <div className="grow" style={{ minWidth: 0 }}>
          <h1>{job.title}</h1>
          <div className="muted" style={{ marginTop: 3 }}>
            <b style={{ color: 'var(--text)' }}>{job.company}</b>
            {' · '}{[job.location, ago(job.age_days), job.source].filter(Boolean).join(' · ')}
          </div>
        </div>
        <Sticker score={job.score} ineligible={job.status === 'ineligible'} size="lg" />
      </div>

      {fit && <p className="summary">{fit.ineligible ? `Ineligible: ${fit.gate}` : fit.summary}</p>}

      <div className="statusbar">
        <StatusPill status={job.status} />
        <select value={job.status} onChange={e => setStatus(e.target.value as Status)} aria-label="Move to">
          {statusOptions.map(([s, label]) => <option key={s} value={s}>{label}</option>)}
        </select>
        <span className="grow" />
        {link && <a className="btn" href={link} target="_blank" rel="noreferrer">Posting ↗</a>}
        <button className="btn" disabled={!!busy || drafting} onClick={score}>
          {busy === 'score' ? <><Spinner /> Scoring…</> : fit ? 'Re-score' : 'Score fit'}</button>
        <button className="btn pop" disabled={drafting || job.status === 'ineligible'} onClick={draft}>
          {drafting ? <><Spinner /> Drafting…</> : job.package ? 'Redraft' : 'Draft package'}</button>
      </div>
      <ErrorBox error={error} />
      {job.last_error && !drafting && <div className="note small">Last draft failed: {job.last_error}</div>}

      {job.package ? (
        <div className="pkg">
          <ResumePanel job={job} onSaved={setJob} />
          <AnswersPanel job={job} />
        </div>
      ) : (
        <div className="empty-card">
          {drafting ? (
            <><h2><Spinner /> Drafting your package</h2>
              <p className="muted">Tailoring a one-page resume and drafting answers. About a minute or two. You can keep browsing.</p></>
          ) : (
            <><h2>No package yet</h2>
              <p className="muted" style={{ marginBottom: 14 }}>Draft a one-page resume tailored to this role, plus answers to its
                application questions. Every line comes from your real experience, and nothing is submitted.</p>
              <button className="btn pop" disabled={job.status === 'ineligible'} onClick={draft}>Draft package</button></>
          )}
        </div>
      )}

      {fit && (
        <details className="fold" open={!job.package}>
          <summary>Why it scored {job.score ?? '–'}</summary>
          <div className="fold-body">
            <div className="bars">
              {PARTS.map(([k, label]) => {
                const s = fit[k] as { score: number; reason: string }
                return (
                  <div key={k} style={{ display: 'contents' }}>
                    <span className="small" style={{ fontWeight: 700 }}>{label}</span>
                    <div className="bar"><div className={s.score >= 75 ? '' : s.score >= 55 ? 'mid' : 'lo'} style={{ width: `${s.score}%` }} /></div>
                    <span className="small mono" style={{ textAlign: 'right' }}>{s.score}</span>
                    <div className="reason">{s.reason}</div>
                  </div>
                )
              })}
            </div>
            {fit.missing.length > 0 && (
              <div className="row" style={{ marginTop: 8, gap: 5 }}>
                <span className="small" style={{ fontWeight: 700 }}>Missing</span>
                {fit.missing.map(m => <span key={m} className="chip orange">{m}</span>)}
              </div>
            )}
          </div>
        </details>
      )}

      <details className="fold">
        <summary>Job description</summary>
        <div className="fold-body">
          {job.description ? <div className="jd">{job.description}</div>
            : <p className="muted">Not fetched yet. It's fetched when the role is scored.</p>}
        </div>
      </details>

      <Notes job={job} />
      {!!job.cost?.total && (
        <p className="small muted">Spent on this role: {money(job.cost.total)} (
          {Object.entries(job.cost.by_kind).map(([k, v]) => `${COST_LABEL[k] ?? k} ${money(v)}`).join(' · ')})</p>
      )}
      {toast}
    </div>
  )
}

function ResumePanel({ job, onSaved }: { job: Job; onSaved: (j: Job) => void }) {
  const pkg = job.package!
  const [t, setT] = useState<Tailored>(pkg.tailored)
  const [bank, setBank] = useState<Profile | null>(null)
  const [editing, setEditing] = useState(false)
  const [bust, setBust] = useState(pkg.created)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setT(pkg.tailored); setBust(pkg.created) }, [pkg])
  useEffect(() => { if (editing && !bank) api.profile().then(setBank) }, [editing, bank])
  const title = (entryId: string) => bank?.experience.find(e => e.id === entryId)?.title ?? entryId

  const save = async () => {
    setBusy(true)
    try { const j = await api.saveTailored(job.id, t); onSaved(j); setBust(Date.now()); setEditing(false) } finally { setBusy(false) }
  }
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="panel-title">
        <h2>Resume</h2>
        {pkg.pages > 1 ? <span className="chip red">{pkg.pages} pages</span> : <span className="chip mint">1 page</span>}
        <span className="grow" />
        <button className="btn small" onClick={() => setEditing(e => !e)}>{editing ? 'Cancel' : 'Edit'}</button>
        <a className="btn small" href={api.pdfUrl(job.id, bust)} target="_blank" rel="noreferrer">PDF ↗</a>
      </div>
      {pkg.tailored.notes.length > 0 && (
        <div className="note small">
          <ul style={{ margin: 0, paddingLeft: 18 }}>{pkg.tailored.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
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
          <div><button className="btn primary" disabled={busy} onClick={save}>{busy ? <Spinner /> : 'Re-render'}</button></div>
        </div>
      ) : (
        <ResumePreview pages={pkg.pages} src={page => api.pngUrl(job.id, page, bust)} />
      )}
    </div>
  )
}

function AnswersPanel({ job }: { job: Job }) {
  const pkg = job.package!
  const [answers, setAnswers] = useState(pkg.answers.answers.map(a => a.answer))
  const [savedQ, setSavedQ] = useState<Set<number>>(new Set())
  const [copied, setCopied] = useState<number | null>(null)
  useEffect(() => { setAnswers(pkg.answers.answers.map(a => a.answer)) }, [pkg])
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="panel-title">
        <h2>Answers</h2>
        <span className={`chip ${pkg.questions_source === 'ats' ? 'mint' : ''}`}>
          {pkg.questions_source === 'ats' ? "posting's real questions" : 'common questions'}</span>
      </div>
      {pkg.answers.gaps.length > 0 && (
        <div className="note small"><b>Only you can answer:</b>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{pkg.answers.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul></div>
      )}
      <div className="card">
        {pkg.answers.answers.length === 0 && <p className="muted">No questions to answer for this posting.</p>}
        {pkg.answers.answers.map((a, i) => (
          <div className="answer" key={i}>
            <div className="row q"><span className="grow">{a.question}</span>
              {a.source === 'learned' && <span className="chip mint">saved</span>}</div>
            <textarea rows={Math.min(8, Math.max(2, Math.ceil(answers[i].length / 70)))} value={answers[i]}
              onChange={e => { const n = [...answers]; n[i] = e.target.value; setAnswers(n) }} />
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn small" onClick={() => { copy(answers[i]); setCopied(i); window.setTimeout(() => setCopied(null), 1200) }}>
                {copied === i ? 'Copied' : 'Copy'}</button>
              {a.source !== 'learned' && (
                <button className="btn small ghost" disabled={savedQ.has(i)} onClick={async () => {
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
