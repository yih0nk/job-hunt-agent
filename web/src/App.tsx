import { useCallback, useEffect, useState } from 'react'
import { api, type AppState } from './api'
import Onboarding from './pages/Onboarding'
import Inbox from './pages/Inbox'
import Review from './pages/Review'
import Board from './pages/Board'
import RecapPage from './pages/Recap'
import ProfilePage from './pages/Profile'
import SearchPage from './pages/Search'
import SettingsPage from './pages/Settings'
import { ErrorBox, money, Spinner } from './ui'

type Page = 'inbox' | 'ready' | 'review' | 'board' | 'recap' | 'profile' | 'search' | 'settings'

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [page, setPage] = useState<Page>('inbox')
  const [onboarding, setOnboarding] = useState<false | 'first' | 'restart'>(false)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)   // bumps when a run finishes, so lists reload

  const refresh = useCallback(async () => {
    try {
      const s = await api.state()
      setState(prev => {
        if (prev?.progress.running && !s.progress.running) setTick(t => t + 1)
        return s
      })
      setError(null)
      return s
    } catch (e) {
      setError(String((e as Error).message))
      return null
    }
  }, [])

  useEffect(() => {
    refresh().then(s => {
      if (s && (!s.has_key || !s.has_profile || !s.has_sources)) setOnboarding('first')
    })
  }, [refresh])

  // Poll quickly while a run is going, slowly otherwise.
  useEffect(() => {
    const id = setInterval(refresh, state?.progress.running ? 1500 : 10000)
    return () => clearInterval(id)
  }, [refresh, state?.progress.running])

  const startRun = async () => {
    try {
      await api.run(true, true)
      await refresh()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  if (!state) {
    return <div className="empty">{error ? <ErrorBox error={`Can't reach the local backend: ${error}`} /> : <Spinner />}</div>
  }

  if (onboarding) {
    return <Onboarding state={state} restart={onboarding === 'restart'} onDone={async () => { setOnboarding(false); await refresh() }}
      onRun={startRun} />
  }

  if (page === 'review') {
    return <Review onChange={refresh} onExit={() => { setPage('inbox'); setTick(t => t + 1) }} />
  }

  const c = state.counts
  const ready = (c.drafted ?? 0) + (c.drafting ?? 0)
  const tracked = (c.applied ?? 0) + (c.interviewing ?? 0) + (c.offer ?? 0)
  const p = state.progress
  const nav = (id: Page, label: string, count?: number) => (
    <button className={page === id ? 'on' : ''} onClick={() => setPage(id)}>
      {label}{!!count && <span className="count">{count}</span>}</button>
  )

  return (
    <div className="app">
      <nav className="nav">
        <div className="brand">
          <span className="brand-mark">J</span>
          <div><b>Job Hunt Agent</b><small>You review. You submit.</small></div>
        </div>
        {nav('inbox', 'Inbox', c.review)}
        {nav('ready', 'Ready to submit', ready)}
        {nav('board', 'Tracker', tracked)}
        {nav('recap', 'Season recap')}
        {nav('profile', 'Profile')}
        {nav('search', 'Search & sources')}
        {nav('settings', 'Settings')}
        <div className="spacer" />
        <div className="runbar">
          {p.running ? (
            <>
              <div className="row"><Spinner /> <b>{p.stage === 'scan' ? 'Scanning' : p.stage === 'score' ? 'Scoring' : 'Drafting'}</b>
                <span className="muted mono">{p.done}/{p.total}</span></div>
              <div className="progress"><div style={{ width: `${p.total ? (100 * p.done) / p.total : 5}%` }} /></div>
              <div className="muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.message}</div>
            </>
          ) : (
            <>
              <button className="btn primary" onClick={startRun} disabled={!state.has_key}>Find &amp; score</button>
              {(c.new ?? 0) > 0 && <span className="muted">{c.new} found, not yet scored</span>}
              {state.last_run?.finished && (
                <span className="muted">Last run {new Date(state.last_run.finished * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                  {typeof state.last_run.summary?.cost === 'number' && ` · ${money(state.last_run.summary.cost as number)}`}</span>
              )}
            </>
          )}
          {!p.running && p.errors.length > 0 && (
            <details><summary className="muted">{p.errors.length} issue(s) last run</summary>
              <ul className="small" style={{ paddingLeft: 16 }}>{p.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
            </details>
          )}
        </div>
      </nav>
      <main className="main">
        {error && <div style={{ padding: 12 }}><ErrorBox error={error} /></div>}
        {(page === 'inbox' || page === 'ready') && (
          <Inbox tick={tick} counts={c} onChange={refresh} onReview={() => setPage('review')} startTab={page === 'ready' ? 1 : 0} />
        )}
        {page === 'board' && <Board tick={tick} onChange={refresh} />}
        {page === 'recap' && <RecapPage />}
        {page === 'profile' && <ProfilePage />}
        {page === 'search' && <SearchPage />}
        {page === 'settings' && <SettingsPage state={state} onChange={refresh} onRerunSetup={() => setOnboarding('restart')} />}
      </main>
    </div>
  )
}
