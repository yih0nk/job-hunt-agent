import { useState, type ReactNode } from 'react'
import type { Status } from './api'

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
      {hint && <span className="small muted" style={{ fontWeight: 400 }}>{hint}</span>}
    </label>
  )
}

export function ScoreNum({ score }: { score: number | null }) {
  if (score === null || score === undefined) return <span className="score lo">–</span>
  const cls = score >= 70 ? 'hi' : score >= 55 ? 'mid' : 'lo'
  return <span className={`score ${cls}`}>{score}</span>
}

const STATUS_PILL: Record<Status, [string, string]> = {
  new: ['Unscored', ''],
  scored: ['Low fit', ''],
  review: ['Good fit', 'accent'],
  ineligible: ['Ineligible', 'bad'],
  drafted: ['Ready to review', 'warn'],
  applied: ['Applied', 'good'],
  interviewing: ['Interviewing', 'good'],
  offer: ['Offer', 'good'],
  rejected: ['Rejected', ''],
  archived: ['Archived', ''],
}

export function StatusPill({ status }: { status: Status }) {
  const [label, cls] = STATUS_PILL[status] ?? [status, '']
  return <span className={`pill ${cls}`}>{label}</span>
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
    <div className="stack" style={{ gap: 8 }}>
      {Array.from({ length: Math.max(1, pages) }, (_, i) => (
        <img key={i} className="page-img" src={src(i)} alt={`Resume page ${i + 1}`} />
      ))}
    </div>
  )
}

export function copy(text: string) {
  void navigator.clipboard.writeText(text)
}
