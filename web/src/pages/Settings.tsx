import { useEffect, useState } from 'react'
import { api, type AppState, type SettingsView } from '../api'
import { ErrorBox, Field, Spinner } from '../ui'
import { MODELS } from './Onboarding'

export default function SettingsPage({ state, onChange, onRerunSetup }: {
  state: AppState; onChange: () => void; onRerunSetup: () => void
}) {
  const [s, setS] = useState<SettingsView | null>(null)
  const [key, setKey] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.settings().then(setS) }, [])
  if (!s) return <div className="page"><Spinner /></div>

  const save = async (patch: { api_key?: string; model?: string; effort?: string }) => {
    setBusy(true); setError(null); setMsg(null)
    try {
      setS(await api.saveSettings(patch))
      if (patch.api_key !== undefined || patch.model) { await api.testKey(); setMsg('Key and model work.') }
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
        <Field label={`API key ${s.has_key ? `(saved, ${s.key_hint})` : '(not set)'}`}>
          <div className="row">
            <input className="grow" style={{ width: 'auto' }} type="password" value={key} autoComplete="off"
              onChange={e => setKey(e.target.value)} placeholder="Paste a new key to replace" />
            <button className="btn" disabled={!key || busy} onClick={() => save({ api_key: key })}>Save key</button>
          </div>
        </Field>
        <div className="grid2">
          <Field label="Model" hint="Used for scoring and drafting.">
            <select value={s.model} onChange={e => save({ model: e.target.value })}>
              {MODELS.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
              {!MODELS.some(m => m.id === s.model) && <option value={s.model}>{s.model}</option>}
            </select>
          </Field>
          <Field label="Effort" hint="Higher effort gives more careful scores and costs more.">
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
        <h2>Your data</h2>
        <p>Everything (profile, jobs, packages, and your API key) lives in <code>{state.data_dir}</code>.
          Nothing is uploaded anywhere except the text sent to Claude for scoring and drafting.</p>
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
