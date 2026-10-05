import { useEffect, useRef, useState } from 'react'
import { toBlob, toPng } from 'html-to-image'
import { api, type Recap } from '../api'
import { ErrorBox, Spinner } from '../ui'

/** Free roasts picked from the numbers, best match first. "Roast me harder" asks Claude. */
function builtInRoasts(r: Recap): string[] {
  const out: string[] = []
  const pct = r.scored ? Math.round((100 * r.good_fits) / r.scored) : 0
  if (r.offers > 0) out.push(`${r.offers} offer${r.offers > 1 ? 's' : ''}. Insufferable. Congrats anyway.`)
  if (r.interviews > 0 && r.offers === 0) out.push(`${r.interviews} interview${r.interviews > 1 ? 's' : ''} in. Wear the lucky socks and stop refreshing your inbox.`)
  if (r.applied > 0 && r.interviews === 0) out.push(`${r.applied} applications, 0 interviews. The ATS has seen things.`)
  if (r.packages > 0 && r.applied === 0) out.push(`${r.packages} package${r.packages > 1 ? 's' : ''} ready, 0 sent. The submit button doesn't bite.`)
  if (r.roles_found > 30 && r.packages === 0) out.push(`${r.roles_found} roles found, 0 drafted. Window shopping isn't a job search.`)
  if (r.roles_found > 30 && r.packages > 0 && r.packages * 20 < r.roles_found)
    out.push(`Looked at ${r.roles_found} roles, committed to ${r.packages}. Picky, or scared?`)
  if (r.scored > 10 && pct < 25) out.push(`Only ${pct}% of the jobs I scored liked you back. Relatable.`)
  out.push(`$${r.spend.toFixed(2)} on AI this season. Cheaper than one latte, and it never lied on your resume.`)
  return out
}

/** The shareable season card: an itemized receipt slapped on a sticker sheet, with the
 *  mascot roasting you. Counts only unless company names are switched on. */
export default function RecapPage() {
  const [r, setR] = useState<Recap | null>(null)
  const [names, setNames] = useState(false)
  const [roast, setRoast] = useState('')
  const [pick, setPick] = useState(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const card = useRef<HTMLDivElement>(null)
  useEffect(() => { api.recap().then(setR).catch(e => setError((e as Error).message)) }, [])
  if (!r) return <div className="page">{error ? <ErrorBox error={error} /> : <Spinner />}</div>

  const free = builtInRoasts(r)
  const line = roast || free[pick % free.length]
  const since = r.since ? new Date(r.since * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' }).toUpperCase() : 'TODAY'
  const opts = { pixelRatio: 2, cacheBust: true }
  const save = async () => {
    if (!card.current) return
    const a = document.createElement('a')
    a.href = await toPng(card.current, opts)
    a.download = 'job-hunt-receipt.png'
    a.click()
    setMsg('Saved job-hunt-receipt.png')
  }
  const copyImg = async () => {
    if (!card.current) return
    const blob = await toBlob(card.current, opts)
    if (!blob) return
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

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Season recap</h1><p>Your search on a receipt, with a free roast. Made to post. Counts only unless you add names.</p></div>
        <div className="row">
          <label className="check small"><input type="checkbox" checked={names} onChange={e => setNames(e.target.checked)} />Show company names</label>
          <button className="btn" onClick={() => { setRoast(''); setPick(p => p + 1) }}>Another roast</button>
          <button className="btn" disabled={busy} onClick={harder}>{busy ? <Spinner /> : 'Roast me harder'}</button>
          <button className="btn" onClick={copyImg}>Copy image</button>
          <button className="btn pop" onClick={save}>Save PNG</button>
        </div>
      </div>
      {msg && <div className="ok" style={{ marginBottom: 14 }}>{msg}</div>}
      <ErrorBox error={error} />

      <div className="rcpt-card" ref={card}>
        <div className="rcpt-title">my job hunt,<br />itemized</div>

        <div className="rcpt">
          <div className="rcpt-head">JOB HUNT AGENT</div>
          <div className="rcpt-sub">SEASON RECEIPT #{no} · SINCE {since}</div>
          <div className="rcpt-rule" />
          {items.map(([k, v]) => (
            <div className="rcpt-line" key={k}><span>{k}</span><span className="rcpt-dots" /><b>{v}</b></div>
          ))}
          {r.best && (
            <div className="rcpt-line"><span>BEST FIT{names ? ` · ${r.best.company.toUpperCase().slice(0, 18)}` : ''}</span>
              <span className="rcpt-dots" /><b className="rcpt-hl">{r.best.score}</b></div>
          )}
          <div className="rcpt-rule" />
          <div className="rcpt-line rcpt-total"><span>LINES INVENTED</span><span className="rcpt-dots" /><b>0</b></div>
          <div className="rcpt-line"><span>AI SPEND</span><span className="rcpt-dots" /><b>${r.spend.toFixed(2)}</b></div>
          {names && r.companies_applied.length > 0 && (
            <div className="rcpt-names">APPLIED: {r.companies_applied.slice(0, 8).join(', ').toUpperCase()}
              {r.companies_applied.length > 8 ? ` +${r.companies_applied.length - 8}` : ''}</div>
          )}
          <div className="rcpt-thanks">THANK YOU FOR HUNTING</div>
          <div className="rcpt-barcode" />
        </div>

        <div className="rcpt-stamp">VERIFIED<br />REAL</div>

        <div className="rcpt-mascot">
          <div className="rcpt-bubble">{line}</div>
          <svg viewBox="0 0 120 120" className="rcpt-bot" aria-hidden="true">
            <rect x="44" y="18" width="32" height="20" rx="5" fill="none" stroke="#111" strokeWidth="7" />
            <rect x="17" y="40" width="94" height="64" rx="10" fill="#111" />
            <rect x="9" y="32" width="94" height="64" rx="10" fill="#FFFBEF" stroke="#111" strokeWidth="7" />
            <circle cx="36" cy="63" r="17" fill="#fff" stroke="#111" strokeWidth="7" />
            <circle cx="76" cy="63" r="17" fill="#fff" stroke="#111" strokeWidth="7" />
            <circle cx="30" cy="58" r="6" fill="#111" />
            <circle cx="70" cy="58" r="6" fill="#111" />
            <path d="M24 40 L48 47 M64 47 L88 40" stroke="#111" strokeWidth="6" strokeLinecap="round" />
          </svg>
        </div>

        <div className="rcpt-sticker pink" style={{ right: '4%', top: '7%', transform: 'rotate(8deg)' }}><b>{r.good_fits}</b> good fits</div>
        <div className="rcpt-sticker mint" style={{ right: '28%', top: '9%', transform: 'rotate(-6deg)' }}><b>{r.roles_found}</b> scanned</div>
        <div className="rcpt-foot">github.com/yih0nk/job-hunt-agent</div>
      </div>
    </div>
  )
}
