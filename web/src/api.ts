// Typed client for the local backend. Types mirror jobhunt/models.py.

export type Link = { label: string; url: string }
export type Bullet = { id: string; text: string }
export type EntryKind = 'work' | 'project' | 'leadership' | 'research' | 'other'
export type Entry = {
  id: string; kind: EntryKind; title: string; org: string; location: string
  start: string; end: string; url: string; tech: string[]; bullets: Bullet[]
}
export type Education = {
  id: string; school: string; degree: string; location: string
  start: string; end: string; gpa: string; details: string[]
}
export type SkillGroup = { name: string; items: string[] }
export type LearnedAnswer = { question: string; answer: string }
export type Profile = {
  name: string; email: string; phone: string; location: string; links: Link[]
  headline: string; work_authorization: string; needs_sponsorship: 'no' | 'yes' | 'depends'
  sponsorship_note: string; education: Education[]; skills: SkillGroup[]
  experience: Entry[]; voice: string; resume_rules: string; learned_answers: LearnedAnswer[]
}

export type SourceKind = 'listing_repo' | 'github_issues' | 'greenhouse' | 'lever' | 'ashby' | 'early_career_radar'
export type Source = {
  id?: string; kind: SourceKind; name: string; enabled: boolean
  repo?: string; branch?: string; file?: string; title_prefix?: string; term?: string
  board?: string; url?: string; accept_tracks?: string[]
}
export type Level = 'internship' | 'new_grad' | 'entry' | 'mid' | 'senior' | 'any'
export type Preferences = {
  level: Level; target_term: string; role_keywords: string[]; exclude_title_keywords: string[]
  exclude_companies: string[]; locations: string[]; countries: string[]; remote_ok: boolean
  max_age_days: number | null; dealbreakers: string; priorities: string; sources: Source[]
  weights: { role_fit: number; skills: number; eligibility: number; level: number; preferences: number }
  thresholds: { auto_draft: number; review: number }
  auto_draft: boolean
}
export type SettingsView = {
  has_key: boolean; key_hint: string; key_in_keychain: boolean
  model: string; score_model: string; effort: string; auto_run_hours: number; logos: boolean
}
export type SettingsPatch = Partial<Pick<SettingsView, 'model' | 'score_model' | 'effort' | 'auto_run_hours' | 'logos'>> & { api_key?: string }
export type JobCost = { total: number; by_kind: Record<string, number> }
export type Usage = {
  today: number; last_30_days: number; all_time: number
  by_kind_30d: Record<string, { calls: number; cost: number }>
}

export type SubScore = { score: number; reason: string }
export type FitScore = {
  ineligible: boolean; gate: string; role_fit: SubScore; skills: SubScore; eligibility: SubScore
  level: SubScore; preferences: SubScore; summary: string; missing: string[]; no_jd?: boolean
}
export type TailoredEntry = { entry_id: string; bullets: string[]; source_bullet_ids: string[] }
export type Tailored = { headline: string; entries: TailoredEntry[]; skills: SkillGroup[]; notes: string[] }
export type DraftAnswer = { question: string; answer: string; source: 'learned' | 'drafted' }
export type Package = {
  created: number; resolved_url: string; pdf: string; pages: number; tailored: Tailored
  answers: { answers: DraftAnswer[]; gaps: string[] }; questions_source: 'ats' | 'generic'
}
export type Status = 'new' | 'scored' | 'review' | 'ineligible' | 'drafting' | 'drafted' | 'applied'
  | 'interviewing' | 'offer' | 'rejected' | 'archived'
export type Job = {
  id: string; company: string; title: string; location: string; url: string; source: string
  age_days: number | null; first_seen: number; description?: string; resolved_url: string
  status: Status; score: number | null; score_detail: FitScore | null; package: Package | null
  notes: string; applied_at: number | null; cost?: JobCost; last_error?: string; outreach?: Outreach | null
}
export type Channel = { kind: string; value: string; source: string }
export type Contact = { name: string; role: string; why: string; source_url: string; channels: Channel[] }
export type Outreach = {
  contacts: Contact[]; email_subject: string; email_body: string; linkedin_note: string; x_dm: string
  notes: string[]; created: number
}
export type Progress = {
  running: boolean; stage: string; done: number; total: number; message: string
  errors: string[]; summary: Record<string, unknown>
}
export type AppState = {
  has_key: boolean; has_profile: boolean; has_sources: boolean
  counts: Partial<Record<Status, number>>
  last_run: { started: number; finished: number | null; summary: Record<string, unknown> | null } | null
  progress: Progress; data_dir: string
}
export type Preset = Source & { for: Level[] }

function token(): string {
  const meta = document.querySelector('meta[name="jobhunt-token"]') as HTMLMetaElement | null
  return meta?.content || import.meta.env.VITE_JOBHUNT_TOKEN || 'dev'
}

export class ApiError extends Error {}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: { 'x-jobhunt-token': token() } }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['content-type'] = 'application/json'
  }
  const r = await fetch(path, init)
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      if (typeof j.detail === 'string') msg = j.detail
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch { /* not json */ }
    throw new ApiError(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  state: () => req<AppState>('GET', '/api/state'),
  profile: () => req<Profile>('GET', '/api/profile'),
  saveProfile: (p: Profile) => req<Profile>('PUT', '/api/profile', p),
  importResume: (f: File) => {
    const fd = new FormData()
    fd.append('file', f)
    return req<Profile>('POST', '/api/profile/import', fd)
  },
  prefs: () => req<Preferences>('GET', '/api/preferences'),
  savePrefs: (p: Preferences) => req<Preferences>('PUT', '/api/preferences', p),
  presets: () => req<Preset[]>('GET', '/api/presets'),
  settings: () => req<SettingsView>('GET', '/api/settings'),
  saveSettings: (s: SettingsPatch) => req<SettingsView>('PUT', '/api/settings', s),
  usage: () => req<Usage>('GET', '/api/usage'),
  testKey: () => req<{ ok: boolean }>('POST', '/api/settings/test'),
  run: (scan = true, score = true) => req<{ started: boolean; progress: Progress }>('POST', '/api/run', { scan, score }),
  progress: () => req<Progress>('GET', '/api/progress'),
  jobs: (statuses: Status[] = []) => req<Job[]>('GET', `/api/jobs?status=${statuses.join(',')}`),
  job: (id: string) => req<Job>('GET', `/api/jobs/${id}`),
  addFromUrl: (url: string) => req<Job>('POST', '/api/jobs/from-url', { url }),
  addJob: (j: { company: string; title: string; url: string; location: string; description: string }) =>
    req<Job>('POST', '/api/jobs', j),
  patchJob: (id: string, p: { status?: Status; notes?: string }) => req<Job>('PATCH', `/api/jobs/${id}`, p),
  score: (id: string) => req<Job>('POST', `/api/jobs/${id}/score`),
  draft: (id: string) => req<Job>('POST', `/api/jobs/${id}/draft`),
  draftLater: (id: string) => req<Job>('POST', `/api/jobs/${id}/draft?background=true`),
  saveTailored: (id: string, t: Tailored) => req<Job>('PUT', `/api/jobs/${id}/tailored`, t),
  fill: (id: string) => req<Job>('POST', `/api/jobs/${id}/fill`),
  refetchDescriptions: () => req<{ checked: number; fixed: number; rescore: string[] }>('POST', '/api/jobs/refetch-descriptions'),
  rescore: (ids: string[]) => req<{ started: number }>('POST', '/api/jobs/rescore', { ids }),
  outreach: (id: string) => req<Job>('POST', `/api/jobs/${id}/outreach`),
  learn: (a: LearnedAnswer) => req<LearnedAnswer[]>('POST', '/api/learned', a),
  pdfUrl: (id: string, bust = 0) => `/api/jobs/${id}/resume.pdf?t=${encodeURIComponent(token())}&v=${bust}`,
  pngUrl: (id: string, page: number, bust = 0) => `/api/jobs/${id}/resume.png?page=${page}&t=${encodeURIComponent(token())}&v=${bust}`,
  logoUrl: (company: string, url = '') =>
    `/api/logo?company=${encodeURIComponent(company)}&url=${encodeURIComponent(url)}&t=${encodeURIComponent(token())}`,
  basePngUrl: (page: number, bust = 0) => `/api/profile/resume.png?page=${page}&t=${encodeURIComponent(token())}&v=${bust}`,
  baseResumeUrl: (bust = 0) => `/api/profile/resume.pdf?t=${encodeURIComponent(token())}&v=${bust}`,
}

export const uid = () => Math.random().toString(16).slice(2, 10)
