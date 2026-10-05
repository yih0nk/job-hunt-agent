import { useState } from 'react'
import { api, type Job } from '../api'
import { copy, ErrorBox, money, Spinner } from '../ui'

const CHANNEL_LABEL: Record<string, string> = { email: 'Email', linkedin: 'LinkedIn', x: 'X', other: 'Link' }

/** Find real people tied to the role and draft a short note to each. Nothing is ever sent:
 *  links open in your browser or mail app and you write from there. */
export default function Outreach({ job, onUpdate }: { job: Job; onUpdate: (j: Job) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState('')
  // Hide the previous result while a new search runs, so stale notes never look current.
  const o = busy ? null : job.outreach
  const run = async () => {
    setBusy(true); setError(null)
    try { onUpdate(await api.outreach(job.id)) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const copyIt = (k: string, text: string) => { copy(text); setCopied(k); window.setTimeout(() => setCopied(''), 1200) }
  const first = (name: string) => name.split(' ')[0]

  return (
    <div className="card stack outreach">
      <div className="card-head">
        <h2>Reach out</h2>
        <p>A short note to a real person at {job.company} gets you read. Nothing is sent for you.</p>
        <span className="grow" />
        <button className="btn small pop" disabled={busy} onClick={run}>
          {busy ? <><Spinner /> Searching…</> : o ? 'Search again' : 'Find people'}</button>
      </div>
      <ErrorBox error={error} />
      {busy && <p className="small muted">Searching for the recruiter, the hiring manager, and engineers on the team. One to two minutes.</p>}
      {!o && !busy && (
        <p className="small muted">Finds up to 4 people publicly tied to this role, with where each was found, and drafts an
          email, a LinkedIn note, and an X DM in your voice. It never guesses an email address. Usually {money(0.10)} to {money(0.30)} per search.</p>
      )}
      {o && (
        <>
          <div className="small muted">Searched {new Date(o.created * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
            {' · '}{o.contacts.length} contact{o.contacts.length === 1 ? '' : 's'} found</div>
          {o.contacts.length === 0 && <div className="note small">No credible contacts found. {o.notes.join(' ')}</div>}
          <div className="contacts">
            {o.contacts.map((c, i) => (
              <div className="contact" key={i}>
                <div className="row" style={{ gap: 6 }}>
                  <b>{c.name}</b><span className="muted small">{c.role}</span>
                </div>
                <div className="small">{c.why}</div>
                <div className="row" style={{ gap: 6, marginTop: 6 }}>
                  {c.channels.map((ch, j) => ch.kind === 'email'
                    ? <a key={j} className="chip mint" href={`mailto:${ch.value}?subject=${encodeURIComponent(o.email_subject)}&body=${encodeURIComponent(o.email_body.replaceAll('{name}', first(c.name)))}`}
                      title={`Found at ${ch.source}`}>✉ {ch.value}</a>
                    : <a key={j} className="chip" href={ch.value} target="_blank" rel="noreferrer" title={`Found at ${ch.source}`}>
                      {CHANNEL_LABEL[ch.kind] ?? 'Link'} ↗</a>)}
                  <a className="chip" href={c.source_url} target="_blank" rel="noreferrer">source ↗</a>
                </div>
              </div>
            ))}
          </div>
          <div className="drafts">
            {([['email', `Subject: ${o.email_subject}\n\n${o.email_body}`, 'Email'],
               ['linkedin', o.linkedin_note, `LinkedIn note · ${o.linkedin_note.length}/300`],
               ['x', o.x_dm, `X DM · ${o.x_dm.length}/280`]] as const).map(([k, text, label]) => (
              <div className="draft" key={k}>
                <div className="row"><b className="small grow">{label}</b>
                  <button className="btn small" onClick={() => copyIt(k, text.replaceAll('{name}', o.contacts[0] ? first(o.contacts[0].name) : 'there'))}>
                    {copied === k ? 'Copied' : 'Copy'}</button></div>
                <div className="draft-text">{text}</div>
              </div>
            ))}
          </div>
          {o.notes.length > 0 && o.contacts.length > 0 && (
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{o.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          )}
        </>
      )}
    </div>
  )
}
