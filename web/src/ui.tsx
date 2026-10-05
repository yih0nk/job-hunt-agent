import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type Job, type Status } from './api'

export function TagInput({ value, onChange, placeholder }: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string
}) {
  const [draft, setDraft] = useState('')
  const add = (raw: string) => {
    const items = raw.split(',').map(s => s.trim()).filter(Boolean).filter(s => !value.includes(s))
    if (items.length) onChange([...value, ...items])
    setDraft('')
  }
  return (
    <div className="tags">
      {value.map(t => (
        <span className="tag" key={t}>{t}
          <button aria-label={`Remove ${t}`} onClick={() => onChange(value.filter(x => x !== t))}>×</button>
        </span>
      ))}
      <input
        value={draft}
        placeholder={placeholder ?? 'Type and press Enter'}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(draft) }
          if (e.key === 'Backspace' && !draft && value.length) onChange(value.slice(0, -1))
        }}
        onBlur={() => draft && add(draft)}
      />
    </div>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      {label}
      {children}
      {hint && <span className="hint small">{hint}</span>}
    </label>
  )
}

export function scoreClass(score: number | null | undefined, ineligible = false): string {
  if (ineligible) return 'bad'
  if (score === null || score === undefined) return 'none'
  return score >= 70 ? 'hi' : score >= 55 ? 'mid' : 'lo'
}

/** The tilted score sticker. */
export function Sticker({ score, ineligible, size }: { score: number | null; ineligible?: boolean; size?: 'lg' | 'xl' }) {
  const label = ineligible ? '✕' : score === null || score === undefined ? '?' : String(score)
  return <span className={`sticker ${scoreClass(score, ineligible)} ${size ?? ''}`}
    title={ineligible ? 'Ineligible' : score === null ? 'Not scored yet' : `Fit ${score}/100`}>{label}</span>
}

/** Back-compat for pages that still show a plain number. */
export function ScoreNum({ score }: { score: number | null }) {
  return <Sticker score={score} />
}

const POPS = ['yellow', 'pink', 'mint', 'lilac', 'orange', 'blue']
function hash(s: string): number {
  let h = 0
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return h
}

/** Company initial on a colour picked from the name, so a company keeps its colour everywhere. */
// Logos that failed this session, so a company missing a logo isn't re-requested per row.
const missingLogos = new Set<string>()

/** Company logo when one can be found, else the initial on a colour picked from the name. */
export function Avatar({ name, url, size }: { name: string; url?: string; size?: 'lg' }) {
  const [failed, setFailed] = useState(missingLogos.has(name))
  const colour = POPS[hash(name.toLowerCase()) % POPS.length]
  const initial = (name.replace(/[^A-Za-z0-9]/g, '')[0] ?? '?').toUpperCase()
  if (!failed) {
    return (
      <span className={`avatar logo ${size ?? ''}`} aria-hidden="true">
        <img src={api.logoUrl(name, url)} alt="" loading="lazy"
          onError={() => { missingLogos.add(name); setFailed(true) }} />
      </span>
    )
  }
  return <span className={`avatar ${size ?? ''}`} style={{ background: `var(--${colour})` }} aria-hidden="true">{initial}</span>
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>
}

const STATUS_CHIP: Record<Status, [string, string]> = {
  new: ['Unscored', ''],
  scored: ['Low fit', ''],
  review: ['Good fit', 'lilac'],
  ineligible: ['Ineligible', 'red'],
  drafting: ['Drafting…', 'pink'],
  drafted: ['Ready', 'yellow'],
  applied: ['Applied', 'mint'],
  interviewing: ['Interviewing', 'blue'],
  offer: ['Offer', 'mint'],
  rejected: ['Rejected', ''],
  archived: ['Archived', ''],
}

export function StatusPill({ status }: { status: Status }) {
  const [label, cls] = STATUS_CHIP[status] ?? [status, '']
  return <span className={`chip ${cls}`}>{label}</span>
}

/** "No Kubernetes experience" -> "no Kubernetes experience": exactly one "no", so gap phrases
 *  from the scorer read the same whether or not they start with a negation. Casing is kept
 *  because gaps are often proper nouns (TensorFlow, AWS). */
export function gapLabel(m: string, max = 28): string {
  const core = m.trim().replace(/^(no|not|lacks?|missing|limited)\b[:\s]*/i, '')
  const text = 'no ' + core
  return text.length > max ? text.slice(0, max - 1) + '…' : text
}

/** Small chips summarising a role: place, freshness, the first gap. */
export function JobChips({ job }: { job: Job }) {
  const city = (job.location || '').split(/[,;·(]/)[0].trim()
  const missing = job.score_detail?.missing?.[0]
  const fresh = job.age_days !== null && job.age_days !== undefined && job.age_days < 2
  return (
    <div className="chips">
      {city && <span className="chip">{city.length > 18 ? city.slice(0, 17) + '…' : city}</span>}
      {job.age_days !== null && job.age_days !== undefined && (
        <span className={`chip ${fresh ? 'pink' : ''}`}>{fresh ? 'new' : ago(job.age_days)}</span>)}
      {job.status === 'drafted' && <span className="chip yellow">ready</span>}
      {job.status === 'drafting' && <span className="chip pink">drafting…</span>}
      {missing && job.status !== 'drafted' && <span className="chip orange" title={missing}>{gapLabel(missing, 22)}</span>}
    </div>
  )
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="error">{error}</div> : null
}

export function Spinner() {
  return <span className="spin" aria-label="Working" />
}

export function ago(days: number | null | undefined): string {
  if (days === null || days === undefined) return ''
  if (days < 1) return 'today'
  if (days < 2) return '1d ago'
  if (days < 30) return `${Math.round(days)}d ago`
  return `${Math.round(days / 30)}mo ago`
}

/** Resume pages as images: renders the same Typst output, and needs no PDF viewer. */
export function ResumePreview({ pages, src }: { pages: number; src: (page: number) => string }) {
  return (
    <div className="stack" style={{ gap: 10 }}>
      {Array.from({ length: Math.max(1, pages) }, (_, i) => (
        <img key={i} className="page-img" src={src(i)} alt={`Resume page ${i + 1}`} />
      ))}
    </div>
  )
}

export function money(usd: number | null | undefined): string {
  if (!usd) return '$0.00'
  return usd < 0.01 ? '<$0.01' : `$${usd.toFixed(2)}`
}

export function copy(text: string) {
  void navigator.clipboard.writeText(text)
}

/** Single-key shortcuts, ignored while typing in a field. */
export function useHotkeys(map: Record<string, () => void>, enabled = true) {
  const ref = useRef(map)
  ref.current = map
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      const fn = ref.current[e.key]
      if (fn) { e.preventDefault(); fn() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}

/** A brief confirmation at the bottom of the screen. */
export function useToast(): [ReactNode, (msg: string) => void] {
  const [msg, setMsg] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const show = (m: string) => {
    setMsg(m)
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setMsg(null), 1800)
  }
  return [msg ? <div className="toast" role="status">{msg}</div> : null, show]
}
