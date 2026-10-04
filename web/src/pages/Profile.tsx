import { useEffect, useState } from 'react'
import { api, uid, type Entry, type EntryKind, type Profile } from '../api'
import { ErrorBox, Field, ResumePreview, Spinner, TagInput } from '../ui'

export function WorkAuthFields({ profile, onChange }: { profile: Profile; onChange: (p: Profile) => void }) {
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => onChange({ ...profile, [k]: v })
  return (
    <div className="stack">
      <div className="grid2">
        <Field label="Work authorization" hint='e.g. "US citizen", "Green card", "F-1 student (CPT/OPT)"'>
          <input value={profile.work_authorization} onChange={e => set('work_authorization', e.target.value)} />
        </Field>
        <Field label="Need visa sponsorship?">
          <select value={profile.needs_sponsorship} onChange={e => set('needs_sponsorship', e.target.value as Profile['needs_sponsorship'])}>
            <option value="no">No</option><option value="yes">Yes</option><option value="depends">It depends</option>
          </select>
        </Field>
      </div>
      {profile.needs_sponsorship !== 'no' && (
        <Field label="How to answer sponsorship questions" hint="The drafter follows this exactly.">
          <textarea value={profile.sponsorship_note} onChange={e => set('sponsorship_note', e.target.value)} />
        </Field>
      )}
    </div>
  )
}

const KINDS: EntryKind[] = ['work', 'project', 'research', 'leadership', 'other']

function EntryEditor({ entry, onChange, onRemove }: { entry: Entry; onChange: (e: Entry) => void; onRemove: () => void }) {
  const set = <K extends keyof Entry>(k: K, v: Entry[K]) => onChange({ ...entry, [k]: v })
  return (
    <div className="entry">
      <div className="entry-head">
        <select style={{ width: 120 }} value={entry.kind} onChange={e => set('kind', e.target.value as EntryKind)}>
          {KINDS.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
        <input className="grow" style={{ width: 'auto', fontWeight: 600 }} value={entry.title} placeholder="Title / project name"
          onChange={e => set('title', e.target.value)} />
        <button className="btn small danger" onClick={onRemove}>Remove</button>
      </div>
      <div className="grid3" style={{ marginBottom: 8 }}>
        <input value={entry.org} placeholder="Company / org" onChange={e => set('org', e.target.value)} />
        <input value={entry.start} placeholder="Start" onChange={e => set('start', e.target.value)} />
        <input value={entry.end} placeholder="End" onChange={e => set('end', e.target.value)} />
      </div>
      <div style={{ marginBottom: 8 }}><TagInput value={entry.tech} onChange={v => set('tech', v)} placeholder="Tech used" /></div>
      {entry.bullets.map((b, i) => (
        <div className="bullet-edit" key={b.id}>
          <textarea rows={2} style={{ minHeight: 0 }} value={b.text} onChange={e => {
            const bullets = [...entry.bullets]; bullets[i] = { ...b, text: e.target.value }; set('bullets', bullets)
          }} />
          <button className="btn small" aria-label="Remove bullet" onClick={() => set('bullets', entry.bullets.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="btn small" onClick={() => set('bullets', [...entry.bullets, { id: uid(), text: '' }])}>+ Bullet</button>
    </div>
  )
}

export default function ProfilePage() {
  const [p, setP] = useState<Profile | null>(null)
  const [saved, setSaved] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [bust, setBust] = useState(0)
  const [showPdf, setShowPdf] = useState(false)
  useEffect(() => { api.profile().then(setP) }, [])
  if (!p) return <div className="page"><Spinner /></div>

  const change = (np: Profile) => { setP(np); setSaved(false) }
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => change({ ...p, [k]: v })
  const save = async () => {
    try { setP(await api.saveProfile(p)); setSaved(true); setBust(b => b + 1); setError(null) } catch (e) { setError((e as Error).message) }
  }
  const reimport = async (f: File | undefined) => {
    if (!f || !confirm('Replace your experience bank with this resume? Unsaved edits are lost.')) return
    setBusy(true)
    try { change(await api.importResume(f)) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Profile</h1><p>Your experience bank. Tailored resumes only ever draw from what's here.</p></div>
        <div className="row">
          {!saved && <span className="chip yellow">Unsaved</span>}
          <label className="btn">{busy ? <Spinner /> : 'Re-import resume'}
            <input type="file" accept=".pdf,.txt,.md,.tex" hidden onChange={e => reimport(e.target.files?.[0])} /></label>
          <button className="btn" onClick={() => { setShowPdf(s => !s); setBust(b => b + 1) }}>{showPdf ? 'Hide' : 'Preview'} base resume</button>
          <button className="btn primary" disabled={saved} onClick={save}>Save</button>
        </div>
      </div>
      <ErrorBox error={error} />
      {showPdf && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="row" style={{ marginBottom: 8 }}><span className="muted small grow">Everything in your bank, rendered with the resume template.
            Tailored resumes pick a subset per job.</span>
            <a className="btn small" href={api.baseResumeUrl(bust)} target="_blank" rel="noreferrer">Open PDF</a></div>
          <ResumePreview pages={1} src={page => api.basePngUrl(page, bust)} />
        </div>
      )}

      <div className="card stack">
        <h2>Basics</h2>
        <div className="grid2">
          <Field label="Full name"><input value={p.name} onChange={e => set('name', e.target.value)} /></Field>
          <Field label="Email"><input value={p.email} onChange={e => set('email', e.target.value)} /></Field>
          <Field label="Phone"><input value={p.phone} onChange={e => set('phone', e.target.value)} /></Field>
          <Field label="Location"><input value={p.location} onChange={e => set('location', e.target.value)} /></Field>
        </div>
        <Field label="Headline"><input value={p.headline} onChange={e => set('headline', e.target.value)} /></Field>
        <Field label="Links">
          <div className="stack" style={{ gap: 6 }}>
            {p.links.map((l, i) => (
              <div className="row" key={i}>
                <input style={{ width: 140 }} value={l.label} placeholder="Label" onChange={e => {
                  const links = [...p.links]; links[i] = { ...l, label: e.target.value }; set('links', links)
                }} />
                <input className="grow" style={{ width: 'auto' }} value={l.url} placeholder="https://" onChange={e => {
                  const links = [...p.links]; links[i] = { ...l, url: e.target.value }; set('links', links)
                }} />
                <button className="btn small" onClick={() => set('links', p.links.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <div><button className="btn small" onClick={() => set('links', [...p.links, { label: '', url: '' }])}>+ Link</button></div>
          </div>
        </Field>
        <WorkAuthFields profile={p} onChange={change} />
        <Field label="Writing voice" hint="How drafted answers should sound.">
          <textarea value={p.voice} onChange={e => set('voice', e.target.value)} />
        </Field>
      </div>

      <div className="card">
        <h2>Education</h2>
        {p.education.map((ed, i) => (
          <div className="entry" key={ed.id}>
            <div className="grid2" style={{ marginBottom: 8 }}>
              {(['school', 'degree', 'start', 'end', 'gpa', 'location'] as const).map(k => (
                <input key={k} value={ed[k]} placeholder={k} onChange={e => {
                  const education = [...p.education]; education[i] = { ...ed, [k]: e.target.value }; set('education', education)
                }} />
              ))}
            </div>
            <div className="row"><span className="grow" />
              <button className="btn small danger" onClick={() => set('education', p.education.filter((_, j) => j !== i))}>Remove</button></div>
          </div>
        ))}
        <button className="btn small" onClick={() => set('education', [...p.education,
          { id: uid(), school: '', degree: '', location: '', start: '', end: '', gpa: '', details: [] }])}>+ Education</button>
      </div>

      <div className="card">
        <h2>Experience &amp; projects</h2>
        <p className="muted small" style={{ marginBottom: 10 }}>Add everything worth featuring, even projects that
          aren't on your current resume. The tailor picks the best 3-5 per job.</p>
        {p.experience.map((en, i) => (
          <EntryEditor key={en.id} entry={en}
            onChange={ne => { const experience = [...p.experience]; experience[i] = ne; set('experience', experience) }}
            onRemove={() => set('experience', p.experience.filter((_, j) => j !== i))} />
        ))}
        <button className="btn small" onClick={() => set('experience', [...p.experience,
          { id: uid(), kind: 'project', title: '', org: '', location: '', start: '', end: '', url: '', tech: [], bullets: [{ id: uid(), text: '' }] }])}>
          + Entry</button>
      </div>

      <div className="card stack">
        <h2>Skills</h2>
        {p.skills.map((g, i) => (
          <div className="row" key={i}>
            <input style={{ width: 160 }} value={g.name} placeholder="Group" onChange={e => {
              const skills = [...p.skills]; skills[i] = { ...g, name: e.target.value }; set('skills', skills)
            }} />
            <div className="grow"><TagInput value={g.items} onChange={v => {
              const skills = [...p.skills]; skills[i] = { ...g, items: v }; set('skills', skills)
            }} /></div>
            <button className="btn small" onClick={() => set('skills', p.skills.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
        <div><button className="btn small" onClick={() => set('skills', [...p.skills, { name: '', items: [] }])}>+ Skill group</button></div>
      </div>

      <div className="card">
        <h2>Saved answers</h2>
        <p className="muted small" style={{ marginBottom: 10 }}>Reused word-for-word whenever an application asks the same question.
          Save one from any drafted package.</p>
        {p.learned_answers.length === 0 && <p className="muted">None yet.</p>}
        {p.learned_answers.map((a, i) => (
          <div className="answer" key={i}>
            <div className="row"><b className="grow">{a.question}</b>
              <button className="btn small danger" onClick={() => set('learned_answers', p.learned_answers.filter((_, j) => j !== i))}>Remove</button></div>
            <textarea value={a.answer} style={{ marginTop: 6 }} onChange={e => {
              const la = [...p.learned_answers]; la[i] = { ...a, answer: e.target.value }; set('learned_answers', la)
            }} />
          </div>
        ))}
      </div>
    </div>
  )
}
