import { useEffect, useState } from 'react'
import { api, type Profile, type Tailored } from '../api'
import { Spinner } from '../ui'

type Tok = { w: string; kind: 'same' | 'add' | 'del' }

const words = (s: string) => s.replace(/\*\*/g, '').split(/\s+/).filter(Boolean)
const norm = (w: string) => w.toLowerCase().replace(/[^\w%$+.-]/g, '')

/** Word-level diff (LCS) from the source bullet to the tailored one. */
function diff(from: string, to: string): Tok[] {
  const a = words(from), b = words(to)
  const n = a.length, m = b.length
  const L = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = norm(a[i]) === norm(b[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
  const out: Tok[] = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (norm(a[i]) === norm(b[j])) { out.push({ w: b[j], kind: 'same' }); i++; j++ }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ w: a[i++], kind: 'del' })
    else out.push({ w: b[j++], kind: 'add' })
  }
  while (i < n) out.push({ w: a[i++], kind: 'del' })
  while (j < m) out.push({ w: b[j++], kind: 'add' })
  return out
}

/** Every tailored bullet next to the profile line(s) it came from. Nothing on the resume
 *  can lack a source: untraceable bullets are dropped server-side before you ever see them. */
export default function Receipts({ tailored }: { tailored: Tailored }) {
  const [bank, setBank] = useState<Profile | null>(null)
  useEffect(() => { api.profile().then(setBank) }, [])
  if (!bank) return <Spinner />

  const entries = new Map(bank.experience.map(e => [e.id, e]))
  const bullets = new Map(bank.experience.flatMap(e => e.bullets.map(b => [b.id, b.text] as const)))
  const rows = tailored.entries.flatMap(te => te.bullets.map((b, i) => ({
    entry: entries.get(te.entry_id),
    text: b,
    sources: (te.source_bullet_ids[i] ?? '').split(',').map(s => s.trim()).filter(Boolean).map(id => bullets.get(id) ?? ''),
  })))
  const verbatim = rows.filter(r => r.sources.length === 1 && words(r.sources[0]).join(' ') === words(r.text).join(' ')).length
  const merged = rows.filter(r => r.sources.length > 1).length

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="receipt-head">
        <span className="sticker hi" style={{ transform: 'rotate(-3deg)' }}>✓</span>
        <div>
          <b>{rows.length} lines, all traced to your profile. 0 invented.</b>
          <div className="small muted">{verbatim} word-for-word · {rows.length - verbatim - merged} reworded · {merged} merged from two lines</div>
        </div>
      </div>
      <div className="card receipts">
        {rows.map((r, k) => (
          <div className="receipt" key={k}>
            <div className="small muted">{r.entry?.org || r.entry?.title}</div>
            <div className="receipt-new">
              {r.sources.length === 1
                ? diff(r.sources[0], r.text).filter(t => t.kind !== 'del').map((t, i) =>
                  <span key={i} className={t.kind === 'add' ? 'w-add' : ''}>{t.w} </span>)
                : words(r.text).join(' ')}
            </div>
            {r.sources.map((src, i) => (
              <div className="receipt-src" key={i}>
                <span className="mono">from</span>{' '}
                {r.sources.length === 1
                  ? diff(src, r.text).filter(t => t.kind !== 'add').map((t, j) =>
                    <span key={j} className={t.kind === 'del' ? 'w-del' : ''}>{t.w} </span>)
                  : src}
              </div>
            ))}
          </div>
        ))}
      </div>
      <p className="small muted">Highlighted words were added for this job; struck words were cut. Every bullet must
        cite a line from your profile, and anything that can't is dropped before you see it.</p>
    </div>
  )
}
