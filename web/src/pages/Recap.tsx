import { useEffect, useRef, useState } from 'react'
import { toBlob, toPng } from 'html-to-image'
import { api, type Recap } from '../api'
import { ErrorBox, Spinner } from '../ui'

/** One line read off the numbers, best match first: hype when you're winning, a shove
 *  when you're slacking. "Roast me" asks Claude for a meaner one. */
function readLines(r: Recap): string[] {
  const out: string[] = []
  const pct = r.scored ? Math.round((100 * r.good_fits) / r.scored) : 0
  // Winning — hype it.
  if (r.offers > 0) out.push(`${r.offers} offer${r.offers > 1 ? 's' : ''} on the board. Okay, superstar — go sign something.`)
  if (r.interviews >= 3) out.push(`${r.interviews} interviews going. You're on a ROLL — do not ghost them.`)
  else if (r.interviews > 0) out.push(`${r.interviews} interview${r.interviews > 1 ? 's' : ''} lined up. Momentum. Keep it.`)
  // Slacking — shove it.
  if (r.applied === 0 && r.packages > 0) out.push(`${r.packages} package${r.packages > 1 ? 's' : ''} ready, 0 sent. Hit submit. Lock in.`)
  if (r.applied === 0 && r.packages === 0 && r.roles_found > 0) out.push(`${r.roles_found} roles found, 0 applied. Lock in — they won't apply to themselves.`)
  if (r.applied >= 8 && r.interviews === 0) out.push(`${r.applied} applications, 0 interviews. The ATS is eating you — tighten the resume.`)
  // Resume signal.
  if (r.scored >= 10 && pct < 25) out.push(`Only ${pct}% scored as good fits. Your resume or your targeting needs a pass.`)
  else if (r.scored >= 10 && pct >= 60) out.push(`${pct}% of roles scored as strong fits. The resume's hitting — now go apply.`)
  if (r.best && r.best.score >= 88) out.push(`Top match scored ${r.best.score}. That one deserves a real cover letter.`)
  if (r.applied > 0 && r.interviews > 0 && r.offers === 0) out.push(`${r.applied} out, ${r.interviews} talking. You're in the game — close one.`)
  // Always-true closer.
  out.push(`$${r.spend.toFixed(2)} of AI this season. Cheaper than a latte, and it never lied on your resume.`)
  return out
}

/** The shareable season card, shaped like a ticket: itemized stub, a one-line read, and the
 *  mascot. Counts only unless company names are switched on. */
export default function RecapPage() {
  const [r, setR] = useState<Recap | null>(null)
  const [names, setNames] = useState(false)
  const [roast, setRoast] = useState('')
  const [pick, setPick] = useState(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const card = useRef<HTMLDivElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  useEffect(() => { api.recap().then(setR).catch(e => setError((e as Error).message)) }, [])

  // Tilt the card toward the cursor and move the glare with it — CSS vars, no re-render.
  const onMove = (e: React.PointerEvent) => {
    const c = card.current
    if (!c) return
    const b = c.getBoundingClientRect()
    const px = (e.clientX - b.left) / b.width, py = (e.clientY - b.top) / b.height
    c.style.setProperty('--ry', `${(px - 0.5) * 14}deg`)
    c.style.setProperty('--rx', `${(0.5 - py) * 10}deg`)
    c.style.setProperty('--mx', `${px * 100}%`)
    c.style.setProperty('--my', `${py * 100}%`)
    stage.current?.classList.add('is-live')
  }
  const reset = () => {
    const c = card.current
    if (c) {
      c.style.setProperty('--rx', '0deg'); c.style.setProperty('--ry', '0deg')
      c.style.setProperty('--mx', '50%'); c.style.setProperty('--my', '50%')
    }
    stage.current?.classList.remove('is-live')
  }
  if (!r) return <div className="page">{error ? <ErrorBox error={error} /> : <Spinner />}</div>

  const free = readLines(r)
  const line = roast || free[pick % free.length]
  const since = r.since ? new Date(r.since * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }).toUpperCase() : 'TODAY'
  const opts = { pixelRatio: 2, cacheBust: true }
  // Flatten the 3D tilt + glare so the shared PNG is the clean 1200x630 ticket.
  const snap = async (fn: (n: HTMLElement, o: typeof opts) => Promise<string | Blob | null>) => {
    const c = card.current
    if (!c) return null
    reset()
    c.classList.add('tkt-flat')
    await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)))
    try { return await fn(c, opts) } finally { c.classList.remove('tkt-flat') }
  }
  const save = async () => {
    const url = await snap(toPng)
    if (typeof url !== 'string') return
    const a = document.createElement('a')
    a.href = url; a.download = 'job-hunt-ticket.png'; a.click()
    setMsg('Saved job-hunt-ticket.png')
  }
  const copyImg = async () => {
    const blob = await snap(toBlob)
    if (!(blob instanceof Blob)) return
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
    setMsg('Copied. Paste it into a post.')
  }
  const harder = async () => {
    setBusy(true); setError(null)
    try { setRoast((await api.roast()).roast) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const items: [string, number | string][] = [
    ['ROLES FOUND', r.roles_found], ['SCORED', r.scored], ['GOOD FITS', r.good_fits],
    ['PACKAGES DRAFTED', r.packages], ['APPLIED', r.applied], ['INTERVIEWS', r.interviews], ['OFFERS', r.offers],
  ]
  const no = String(Math.abs(Math.round((r.since ?? 0) % 100000))).padStart(5, '0')
  // The stub's headline: the furthest you've gotten this season.
  const big = r.offers > 0 ? { n: r.offers, label: r.offers > 1 ? 'OFFERS' : 'OFFER' }
    : r.interviews > 0 ? { n: r.interviews, label: r.interviews > 1 ? 'INTERVIEWS' : 'INTERVIEW' }
      : { n: r.good_fits, label: 'GOOD FITS' }

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Season recap</h1><p>Your search as a ticket, with a one-line read. Made to post. Counts only unless you add names.</p></div>
        <div className="row">
          <label className="check small"><input type="checkbox" checked={names} onChange={e => setNames(e.target.checked)} />Show company names</label>
          <button className="btn" onClick={() => { setRoast(''); setPick(p => p + 1) }}>Another line</button>
          <button className="btn" disabled={busy} onClick={harder}>{busy ? <Spinner /> : 'Roast me'}</button>
          <button className="btn" onClick={copyImg}>Copy image</button>
          <button className="btn pop" onClick={save}>Save PNG</button>
        </div>
      </div>
      {msg && <div className="ok" style={{ marginBottom: 14 }}>{msg}</div>}
      <ErrorBox error={error} />

      <div className="tkt-stage" ref={stage} onPointerMove={onMove} onPointerLeave={reset}>
        <div className="tkt-card" ref={card}>
          <div className="tkt">
            <div className="tkt-main">
              <div className="tkt-band"><span>JOB HUNT AGENT</span><span>SEASON PASS · {since}</span></div>
              <div className="tkt-title">my job hunt,<br />itemized</div>
              <div className="tkt-rows">
                {items.map(([k, v]) => (
                  <div className="tkt-row" key={k}><span>{k}</span><span className="tkt-dots" /><b>{v}</b></div>
                ))}
                {r.best && (
                  <div className="tkt-row"><span>BEST FIT{names ? ` · ${r.best.company.toUpperCase().slice(0, 16)}` : ''}</span>
                    <span className="tkt-dots" /><b className="tkt-hl">{r.best.score}</b></div>
                )}
                <div className="tkt-rule" />
                <div className="tkt-row"><span>LINES INVENTED</span><span className="tkt-dots" /><b>0</b></div>
                <div className="tkt-row"><span>AI SPEND</span><span className="tkt-dots" /><b>${r.spend.toFixed(2)}</b></div>
                {names && r.companies_applied.length > 0 && (
                  <div className="tkt-names">APPLIED: {r.companies_applied.slice(0, 7).join(', ').toUpperCase()}
                    {r.companies_applied.length > 7 ? ` +${r.companies_applied.length - 7}` : ''}</div>
                )}
              </div>
              <div className="tkt-verdict">
                <span className="tkt-verdict-k">THE VERDICT</span>
                <p>{line}</p>
              </div>
            </div>

            <div className="tkt-stub">
              <div className="tkt-admit">ADMIT ONE</div>
              <div className="tkt-big"><b>{big.n}</b><span>{big.label}</span></div>
              <svg viewBox="0 0 120 120" className="tkt-bot" aria-hidden="true">
                <rect x="44" y="18" width="32" height="20" rx="5" fill="none" stroke="#1a1a1a" strokeWidth="7" />
                <rect x="17" y="40" width="94" height="64" rx="10" fill="#1a1a1a" />
                <rect x="9" y="32" width="94" height="64" rx="10" fill="#fdf6e8" stroke="#1a1a1a" strokeWidth="7" />
                <circle cx="36" cy="63" r="17" fill="#fff" stroke="#1a1a1a" strokeWidth="7" />
                <circle cx="76" cy="63" r="17" fill="#fff" stroke="#1a1a1a" strokeWidth="7" />
                <circle cx="30" cy="58" r="6" fill="#1a1a1a" />
                <circle cx="70" cy="58" r="6" fill="#1a1a1a" />
                <path d="M24 40 L48 47 M64 47 L88 40" stroke="#1a1a1a" strokeWidth="6" strokeLinecap="round" />
              </svg>
              <div className="tkt-serial">No. {no}<br />{since}</div>
              <div className="tkt-barcode" />
            </div>

            <div className="tkt-foot">github.com/yih0nk/job-hunt-agent</div>
          </div>
          <div className="tkt-glare" aria-hidden="true" />
        </div>
      </div>
    </div>
  )
}
