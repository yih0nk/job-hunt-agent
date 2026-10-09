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

      <LocalModel s={s} save={save} />

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
        <div className="row" style={{ alignItems: 'center' }}>
          <b>Spend cap</b>
          <span className="small muted">$</span>
          <input type="number" min={0} step={0.5} style={{ width: 90 }} key={s.spend_cap_usd} defaultValue={s.spend_cap_usd || ''}
            placeholder="none" disabled={busy}
            onBlur={e => { const n = Number(e.target.value) || 0; if (n !== s.spend_cap_usd) void save({ spend_cap_usd: n }) }}
            onKeyDown={e => e.key === 'Enter' && (e.target as HTMLInputElement).blur()} />
          <select style={{ width: 110 }} value={s.spend_cap_per} disabled={busy}
            onChange={e => save({ spend_cap_per: e.target.value as 'run' | 'day' })}>
            <option value="day">per day</option>
            <option value="run">per run</option>
          </select>
          {s.spend_cap_usd > 0 && usage && s.spend_cap_per === 'day' && (
            <span className="small muted">{money(usage.today)} of {money(s.spend_cap_usd)} used today</span>
          )}
        </div>
        <p className="small muted">A run stops scoring and auto-drafting when it hits the cap; unscored roles just wait for the next run.
          Leave empty for no cap. Things you trigger on one role (Score, Draft, Reach out) aren't blocked.</p>
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

/** Run scoring and/or drafting on a model on this computer via Ollama: free and private.
 *  Resume import and outreach still need Claude (they read PDFs and search the web). */
function LocalModel({ s, save }: { s: SettingsView; save: (p: SettingsPatch) => Promise<void> }) {
  const [models, setModels] = useState<string[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [url, setUrl] = useState(s.local_url)
  const check = async () => {
    setErr(null)
    try { setModels((await api.localModels()).models) } catch (e) { setModels(null); setErr((e as Error).message) }
  }
  useEffect(() => { void check() }, [s.local_url])
  const usingLocal = s.score_provider === 'local' || s.draft_provider === 'local'
  return (
    <div className="card stack">
      <div className="card-head"><h2>Local model</h2><p>Free and private: runs on this computer with Ollama.</p></div>
      <div className="grid2">
        <Field label="Score roles with">
          <select value={s.score_provider} onChange={e => save({ score_provider: e.target.value as 'claude' | 'local' })}>
            <option value="claude">Claude (scoring model above)</option><option value="local">Local model, $0</option>
          </select>
        </Field>
        <Field label="Draft packages with" hint="Smaller local models write noticeably weaker resumes. Claude is recommended here.">
          <select value={s.draft_provider} onChange={e => save({ draft_provider: e.target.value as 'claude' | 'local' })}>
            <option value="claude">Claude (drafting model above)</option><option value="local">Local model, $0</option>
          </select>
        </Field>
      </div>
      <div className="grid2">
        <Field label="Model">
          <div className="row">
            <select className="grow" style={{ width: 'auto' }} value={s.local_model} onChange={e => save({ local_model: e.target.value })}>
              <option value="">{models?.length ? 'Pick a model' : 'No models found'}</option>
              {(models ?? []).map(m => <option key={m} value={m}>{m}</option>)}
            </select>
            <button className="btn small" onClick={check}>Refresh</button>
          </div>
        </Field>
        <Field label="Ollama address">
          <input value={url} onChange={e => setUrl(e.target.value)} onBlur={() => url !== s.local_url && save({ local_url: url })} />
        </Field>
      </div>
      {err && (
        <div className={usingLocal ? 'note small' : 'small muted'}>
          {err} To set it up: install Ollama from <a href="https://ollama.com" target="_blank" rel="noreferrer">ollama.com</a>,
          then run <code>ollama pull qwen3:14b</code> (or any model your machine can hold) and press Refresh.
        </div>
      )}
      <p className="small muted">Resume import and outreach always use Claude: they read PDFs and search the web.</p>
    </div>
  )
}
