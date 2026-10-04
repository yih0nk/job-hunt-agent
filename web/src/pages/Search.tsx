import { useEffect, useState } from 'react'
import { api, uid, type Level, type Preferences, type Preset, type Source, type SourceKind } from '../api'
import { ErrorBox, Field, Spinner, TagInput } from '../ui'

const LEVELS: [Level, string][] = [
  ['internship', 'Internship / co-op'], ['new_grad', 'New grad'], ['entry', 'Entry level'],
  ['mid', 'Mid level'], ['senior', 'Senior+'], ['any', 'Any level'],
]

export function SearchForm({ prefs, onChange }: { prefs: Preferences; onChange: (p: Preferences) => void }) {
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => onChange({ ...prefs, [k]: v })
  return (
    <div className="stack">
      <div className="grid2">
        <Field label="Level">
          <select value={prefs.level} onChange={e => set('level', e.target.value as Level)}>
            {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <Field label="Target term (optional)" hint='e.g. "Summer 2027". Roles for other terms are gated.'>
          <input value={prefs.target_term} onChange={e => set('target_term', e.target.value)} />
        </Field>
      </div>
      <Field label="Role keywords" hint="A title must contain one of these to be considered.">
        <TagInput value={prefs.role_keywords} onChange={v => set('role_keywords', v)} />
      </Field>
      <Field label="Exclude titles containing">
        <TagInput value={prefs.exclude_title_keywords} onChange={v => set('exclude_title_keywords', v)} />
      </Field>
      <div className="grid2">
        <Field label="Preferred locations" hint="Leave empty to accept anywhere in the countries below.">
          <TagInput value={prefs.locations} onChange={v => set('locations', v)} placeholder="San Francisco, NYC, …" />
        </Field>
        <Field label="Countries you can work in">
          <TagInput value={prefs.countries} onChange={v => set('countries', v)} />
        </Field>
      </div>
      <div className="row">
        <label className="check"><input type="checkbox" checked={prefs.remote_ok}
          onChange={e => set('remote_ok', e.target.checked)} /> Remote is fine</label>
        <span className="grow" />
        <label className="check small">Only roles posted in the last
          <input type="number" style={{ width: 64 }} min={1} value={prefs.max_age_days ?? ''}
            onChange={e => set('max_age_days', e.target.value ? Number(e.target.value) : null)} /> days</label>
      </div>
      <Field label="Exclude companies">
        <TagInput value={prefs.exclude_companies} onChange={v => set('exclude_companies', v)} />
      </Field>
      <Field label="Dealbreakers" hint="Plain English. Roles that hit these are marked ineligible, e.g. 'no defense or gambling companies', 'must be hybrid at most'.">
        <textarea value={prefs.dealbreakers} onChange={e => set('dealbreakers', e.target.value)} />
      </Field>
      <Field label="What you care about" hint="Used for the preferences score, e.g. 'early-stage startups, developer tools, strong mentorship'.">
        <textarea value={prefs.priorities} onChange={e => set('priorities', e.target.value)} />
      </Field>
    </div>
  )
}

const KIND_LABEL: Record<SourceKind, string> = {
  listing_repo: 'GitHub listing repo', github_issues: 'GitHub pending submissions',
  greenhouse: 'Greenhouse board', lever: 'Lever board', ashby: 'Ashby board',
  early_career_radar: 'Early Career Radar',
}

export function SourceList({ prefs, presets, onChange }: {
  prefs: Preferences; presets: Preset[]; onChange: (p: Preferences) => void
}) {
  const [kind, setKind] = useState<'greenhouse' | 'lever' | 'ashby' | 'listing_repo'>('greenhouse')
  const [value, setValue] = useState('')
  const setSources = (s: Source[]) => onChange({ ...prefs, sources: s })
  const has = (p: Preset) => prefs.sources.some(s => s.kind === p.kind && (s.repo ?? '') === (p.repo ?? '') && (s.url ?? '') === (p.url ?? ''))

  const add = () => {
    const v = value.trim()
    if (!v) return
    const src: Source = kind === 'listing_repo'
      ? { id: uid(), kind, name: v, enabled: true, repo: v.replace(/^https?:\/\/github\.com\//, '').replace(/\/$/, ''), branch: 'main', file: 'README.md' }
      : { id: uid(), kind, name: v, enabled: true, board: v.toLowerCase().replace(/\s+/g, '') }
    setSources([...prefs.sources, src])
    setValue('')
  }

  return (
    <div className="stack">
      <div className="stack" style={{ gap: 6 }}>
        {prefs.sources.length === 0 && <p className="muted">No sources yet. Add a suggested list below, or a company's job board.</p>}
        {prefs.sources.map((s, i) => (
          <div className="row" key={s.id ?? i}>
            <label className="check grow">
              <input type="checkbox" checked={s.enabled} onChange={e => {
                const next = [...prefs.sources]; next[i] = { ...s, enabled: e.target.checked }; setSources(next)
              }} />
              <span><b>{s.name || s.repo || s.board}</b> <span className="muted small">{KIND_LABEL[s.kind]}</span></span>
            </label>
            {s.kind === 'listing_repo' && (
              <span className="small muted">branch <input style={{ width: 70, padding: '2px 4px' }} value={s.branch ?? 'main'}
                onChange={e => { const next = [...prefs.sources]; next[i] = { ...s, branch: e.target.value }; setSources(next) }} /></span>
            )}
            <button className="btn small danger" onClick={() => setSources(prefs.sources.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
      </div>
      {presets.some(p => !has(p)) && (
        <div>
          <div className="small muted" style={{ marginBottom: 6 }}>Suggested lists</div>
          <div className="row">
            {presets.filter(p => !has(p)).map(p => (
              <button key={p.name} className="btn small" onClick={() => {
                const { for: _for, ...src } = p
                void _for
                setSources([...prefs.sources, { ...src, id: uid(), enabled: true }])
              }}>+ {p.name}{p.for.includes(prefs.level) ? '' : ' (other level)'}</button>
            ))}
          </div>
        </div>
      )}
      <div>
        <div className="small muted" style={{ marginBottom: 6 }}>Watch a specific company or list</div>
        <div className="row">
          <select style={{ width: 190 }} value={kind} onChange={e => setKind(e.target.value as typeof kind)}>
            <option value="greenhouse">Greenhouse board</option>
            <option value="lever">Lever board</option>
            <option value="ashby">Ashby board</option>
            <option value="listing_repo">GitHub listing repo</option>
          </select>
          <input className="grow" style={{ width: 'auto' }} value={value} onChange={e => setValue(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && add()}
            placeholder={kind === 'listing_repo' ? 'owner/repo' : 'board name, e.g. stripe (from boards.greenhouse.io/stripe)'} />
          <button className="btn" onClick={add}>Add</button>
        </div>
      </div>
    </div>
  )
}

export default function SearchPage() {
  const [prefs, setPrefs] = useState<Preferences | null>(null)
  const [presets, setPresets] = useState<Preset[]>([])
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    Promise.all([api.prefs(), api.presets()]).then(([p, ps]) => { setPrefs(p); setPresets(ps) })
  }, [])
  if (!prefs) return <div className="page"><Spinner /></div>
  const change = (p: Preferences) => { setPrefs(p); setSaved(false) }
  const save = async () => {
    try { setPrefs(await api.savePrefs(prefs)); setSaved(true); setError(null) } catch (e) { setError((e as Error).message) }
  }
  const w = prefs.weights
  const setW = (k: keyof typeof w, v: number) => change({ ...prefs, weights: { ...w, [k]: v } })
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Search &amp; sources</h1><p>What to look for and where. Changes apply to the next run.</p></div>
        <div className="row">{saved && <span className="chip mint">Saved</span>}
          <button className="btn primary" onClick={save}>Save</button></div>
      </div>
      <ErrorBox error={error} />
      <div className="card"><h2>Search</h2><SearchForm prefs={prefs} onChange={change} /></div>
      <div className="card"><h2>Sources</h2><SourceList prefs={prefs} presets={presets} onChange={change} /></div>
      <div className="card stack">
        <h2>Scoring</h2>
        <p className="muted small">Each role gets five sub-scores (0-100), combined with these weights into one fit score.</p>
        <div className="grid3">
          {(Object.keys(w) as (keyof typeof w)[]).map(k => (
            <Field key={k} label={k.replace('_', ' ')}>
              <input type="number" min={0} max={100} value={w[k]} onChange={e => setW(k, Number(e.target.value))} />
            </Field>
          ))}
        </div>
        <div className="grid3">
          <Field label="Show in Inbox at score ≥">
            <input type="number" value={prefs.thresholds.review}
              onChange={e => change({ ...prefs, thresholds: { ...prefs.thresholds, review: Number(e.target.value) } })} />
          </Field>
          <Field label="Auto-draft at score ≥">
            <input type="number" value={prefs.thresholds.auto_draft}
              onChange={e => change({ ...prefs, thresholds: { ...prefs.thresholds, auto_draft: Number(e.target.value) } })} />
          </Field>
          <label className="check" style={{ alignSelf: 'end', paddingBottom: 8 }}>
            <input type="checkbox" checked={prefs.auto_draft} onChange={e => change({ ...prefs, auto_draft: e.target.checked })} />
            Draft packages automatically after each run</label>
        </div>
      </div>
    </div>
  )
}
