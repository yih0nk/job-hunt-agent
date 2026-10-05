import { useEffect, useState } from 'react'
import { api, type AppState, type SettingsPatch, type SettingsView, type Usage } from '../api'
import { ErrorBox, Field, money, Spinner } from '../ui'
import { MODELS } from './Onboarding'

const KIND_LABEL: Record<string, string> = {
  score: 'Scoring roles', tailor: 'Tailoring resumes', answers: 'Drafting answers', parse: 'Importing resumes',
}

function ModelSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)}>
      {MODELS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
      {!MODELS.some(m => m.id === value) && <option value={value}>{value}</option>}
    </select>
  )
}

export default function SettingsPage({ state, onChange, onRerunSetup }: {
  state: AppState; onChange: () => void; onRerunSetup: () => void
}) {
  const [s, setS] = useState<SettingsView | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [key, setKey] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.settings().then(setS)
    api.usage().then(setUsage)
  }, [])
  if (!s) return <div className="page"><Spinner /></div>

  const save = async (patch: SettingsPatch) => {
    setBusy(true); setError(null); setMsg(null)
    try {
      setS(await api.saveSettings(patch))
      if (patch.api_key !== undefined || patch.model || patch.score_model) { await api.testKey(); setMsg('Key and models work.') }
      else setMsg('Saved.')
      setKey('')
      onChange()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="page">
      <div className="page-head"><div><h1>Settings</h1></div></div>
      <div className="card stack">
        <h2>Claude</h2>
        <Field label={`API key ${s.has_key ? `(saved, ${s.key_hint})` : '(not set)'}`}
          hint={s.has_key ? (s.key_in_keychain ? 'Stored in your system keychain.'
            : 'No system keychain available, so it is stored in the local app database.') : undefined}>
          <div className="row">
            <input className="grow" style={{ width: 'auto' }} type="password" value={key} autoComplete="off"
              onChange={e => setKey(e.target.value)} placeholder="Paste a new key to replace" />
            <button className="btn" disabled={!key || busy} onClick={() => save({ api_key: key })}>Save key</button>
          </div>
        </Field>
        <div className="grid2">
          <Field label="Scoring model" hint="Runs on every new role, so a cheaper model saves the most here.">
            <ModelSelect value={s.score_model} onChange={v => save({ score_model: v })} />
          </Field>
          <Field label="Drafting model" hint="Writes tailored resumes and answers, and imports your resume.">
            <ModelSelect value={s.model} onChange={v => save({ model: v })} />
          </Field>
          <Field label="Effort" hint="Higher effort thinks longer: more careful, costs more.">
            <select value={s.effort} onChange={e => save({ effort: e.target.value })}>
              <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
            </select>
          </Field>
        </div>
        {busy && <Spinner />}
        {msg && <div className="ok">{msg}</div>}
        <ErrorBox error={error} />
      </div>

      <div className="card stack">
        <h2>Automatic runs</h2>
        <Field label="Find & score new roles" hint="Only while the app is open. Each run scores up to 60 new roles.">
          <select value={s.auto_run_hours} onChange={e => save({ auto_run_hours: Number(e.target.value) })}>
            <option value={0}>Only when I click the button</option>
            <option value={3}>Every 3 hours</option>
            <option value={6}>Every 6 hours</option>
            <option value={12}>Every 12 hours</option>
            <option value={24}>Once a day</option>
          </select>
        </Field>
      </div>

      <div className="card stack">
        <h2>Spend</h2>
        {!usage ? <Spinner /> : (
          <>
            <div className="grid3">
              <div><div className="small muted">Today</div><div className="score" style={{ textAlign: 'left' }}>{money(usage.today)}</div></div>
              <div><div className="small muted">Last 30 days</div><div className="score" style={{ textAlign: 'left' }}>{money(usage.last_30_days)}</div></div>
              <div><div className="small muted">All time</div><div className="score" style={{ textAlign: 'left' }}>{money(usage.all_time)}</div></div>
            </div>
            {Object.keys(usage.by_kind_30d).length > 0 && (
              <div className="bars" style={{ gridTemplateColumns: '160px 1fr 70px' }}>
                {Object.entries(usage.by_kind_30d).map(([k, v]) => (
                  <div key={k} style={{ display: 'contents' }}>
                    <span className="small">{KIND_LABEL[k] ?? k}</span>
                    <span className="small muted">{v.calls} call{v.calls === 1 ? '' : 's'}</span>
                    <span className="small" style={{ textAlign: 'right' }}>{money(v.cost)}</span>
                  </div>
                ))}
              </div>
            )}
            <p className="small muted">Estimated from token counts at list prices. Your Anthropic Console has the exact bill.</p>
          </>
        )}
      </div>

      <div className="card stack">
        <h2>Your data</h2>
        <p>Your profile, jobs, and packages live in <code>{state.data_dir}</code>; your API key is in the system
          keychain. Nothing is uploaded anywhere except the text sent to Claude for scoring and drafting.</p>
        <label className="check">
          <input type="checkbox" checked={s.logos} onChange={e => save({ logos: e.target.checked })} />
          Show company logos
        </label>
        <p className="small muted" style={{ marginTop: -6 }}>Looks up each company's logo once by name (Clearbit) and
          domain (Google), then keeps it on this computer. Only company names are sent.</p>
        <div><button className="btn" onClick={onRerunSetup}>Run setup again</button></div>
      </div>
      <div className="card stack">
        <h2>What this app will never do</h2>
        <p className="muted">Submit an application, create accounts, enter passwords, solve CAPTCHAs, or invent
          experience. It prepares everything up to the submit button, and you take it from there.</p>
      </div>
    </div>
  )
}
