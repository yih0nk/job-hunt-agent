import { useEffect, useState } from 'react'
import { api, type AppState, type Preferences, type Preset, type Profile } from '../api'
import { ErrorBox, Spinner } from '../ui'
import { SearchForm, SourceList } from './Search'
import { WorkAuthFields } from './Profile'

// Per million tokens, input / output. Shown so the cost tradeoff is visible when picking.
const MODELS = [
  { id: 'claude-opus-5', label: 'Claude Opus 5 · most careful · $5 / $25' },
  { id: 'claude-sonnet-5', label: 'Claude Sonnet 5 · balanced · $2 / $10' },
  { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5 · cheapest · $1 / $5' },
]
export { MODELS }

export default function Onboarding({ state, restart, onDone, onRun }: {
  state: AppState; restart?: boolean; onDone: () => void; onRun: () => Promise<void>
}) {
  const first = restart || !state.has_key ? 0 : !state.has_profile ? 1 : !state.has_sources ? 2 : 3
  const [step, setStep] = useState(first)
  return (
    <div className="onboard">
      <h1>Set up your job search</h1>
      <p className="muted">Everything stays on this computer. The app finds roles, scores them against your
        background, and drafts tailored resumes and answers. You review and submit every application yourself.</p>
      <div className="steps">{[0, 1, 2, 3].map(i => <div key={i} className={i <= step ? 'on' : ''} />)}</div>
      {step === 0 && <KeyStep next={() => setStep(1)} />}
      {step === 1 && <ResumeStep next={() => setStep(2)} back={() => setStep(0)} />}
      {step === 2 && <SearchStep next={() => setStep(3)} back={() => setStep(1)} />}
      {step === 3 && (
        <div className="card stack">
          <h2>You're set</h2>
          <p>The first run scans your sources and scores the newest matches. It can take a few minutes. Roles
            scoring {'>='}55 show up in your Inbox. Open one to draft a tailored package.</p>
          <div className="row">
            <button className="btn primary" onClick={async () => { await onRun(); onDone() }}>Run first scan</button>
            <button className="btn" onClick={onDone}>Skip for now</button>
          </div>
        </div>
      )}
    </div>
  )
}

function KeyStep({ next }: { next: () => void }) {
  const [key, setKey] = useState('')
  const [model, setModel] = useState('claude-opus-5')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasKey, setHasKey] = useState(false)
  useEffect(() => { api.settings().then(s => { setHasKey(s.has_key); setModel(s.model) }) }, [])
  const save = async () => {
    setBusy(true); setError(null)
    try {
      await api.saveSettings({ ...(key ? { api_key: key } : {}), model })
      await api.testKey()
      next()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <div className="card stack">
      <h2>1. Connect Claude</h2>
      <p className="muted">The app calls Claude with your own Anthropic API key. Create one at{' '}
        <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">console.anthropic.com</a>.
        It's kept in your system keychain on this computer. Roles are scored with Claude Sonnet 5 (about a cent or two
        each); the model below writes your tailored resumes and answers. Both can be changed later in Settings.</p>
      <label className="field">API key
        <input type="password" value={key} onChange={e => setKey(e.target.value)}
          placeholder={hasKey ? 'Saved. Paste a new key to replace it.' : 'sk-ant-…'} autoComplete="off" />
      </label>
      <label className="field">Model for drafting packages
        <select value={model} onChange={e => setModel(e.target.value)}>
          {MODELS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
      </label>
      <ErrorBox error={error} />
      <div className="row">
        <button className="btn primary" disabled={busy || (!key && !hasKey)} onClick={save}>
          {busy ? <><Spinner /> Checking…</> : 'Save & continue'}</button>
      </div>
    </div>
  )
}

function ResumeStep({ next, back }: { next: () => void; back: () => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.profile().then(p => { if (p.name) setProfile(p) }) }, [])

  const upload = async (f: File | undefined) => {
    if (!f) return
    setBusy(true); setError(null)
    try { setProfile(await api.importResume(f)) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const save = async () => {
    if (!profile) return
    await api.saveProfile(profile)
    next()
  }
  const bullets = profile?.experience.reduce((n, e) => n + e.bullets.length, 0) ?? 0
  return (
    <div className="card stack">
      <h2>2. Import your resume</h2>
      <p className="muted">Claude reads it into an experience bank: your jobs, projects, and bullets. Tailored
        resumes can only reuse and reword what's in the bank. It never invents experience. You can add more
        projects later in Profile.</p>
      <label className="drop">
        <input type="file" accept=".pdf,.txt,.md,.tex" onChange={e => upload(e.target.files?.[0])} />
        {busy ? <><Spinner /> Reading your resume…</> : profile ? 'Import a different resume' : 'Choose a PDF (or .txt / .md)'}
      </label>
      <ErrorBox error={error} />
      {profile && (
        <>
          <div className="ok">Found <b>{profile.name || 'no name'}</b>, {profile.education.length} school(s),{' '}
            {profile.experience.length} experience entries, {bullets} bullets.</div>
          <WorkAuthFields profile={profile} onChange={setProfile} />
        </>
      )}
      <div className="row">
        <button className="btn" onClick={back}>Back</button>
        <button className="btn primary" disabled={!profile} onClick={save}>Save & continue</button>
      </div>
    </div>
  )
}

function SearchStep({ next, back }: { next: () => void; back: () => void }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [presets, setPresets] = useState<Preset[]>([])
  useEffect(() => {
    Promise.all([api.prefs(), api.presets()]).then(([p, ps]) => { setPrefs(p); setPresets(ps) })
  }, [])
  if (!prefs) return <Spinner />
  const save = async () => { await api.savePrefs(prefs); next() }
  return (
    <div className="stack">
      <div className="card stack">
        <h2>3. What are you looking for?</h2>
        <SearchForm prefs={prefs} onChange={setPrefs} />
      </div>
      <div className="card stack">
        <h2>Where to look</h2>
        <SourceList prefs={prefs} presets={presets} onChange={setPrefs} />
      </div>
      <div className="row">
        <button className="btn" onClick={back}>Back</button>
        <button className="btn primary" disabled={!prefs.sources.some(s => s.enabled)} onClick={save}>Save & continue</button>
      </div>
    </div>
  )
}
