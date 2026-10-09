# Sourcing: how every job gets onto the platform

The goal is simple to state and hard to do: **if a role you'd want exists, it shows up here within
a day of being posted — and nothing you've already applied to ever shows up again.**

This document is the deep run on that. It maps where jobs actually live, what each source gives us
(and through which door), how we turn "a list of companies" into "every posting at those companies",
and how the tracker import closes the loop so the scanner never re-surfaces what you've done.

Everything here follows one rule: **public, documented or plainly-public JSON, polite.** We read the
same endpoints a company's own careers page reads in your browser. No logins, no scraping behind
auth, no LinkedIn/Indeed, no CAPTCHAs. Each fetcher is a GET (or one POST for Workday) with a clear
User-Agent and no concurrency per host.

---

## 1. Where jobs live (the five tiers)

Jobs don't live in one place. They cascade through five tiers, and each tier is a different kind
of source with different freshness, coverage, and data quality.

| Tier | What it is | Coverage | Freshness | Gives a JD? | Example |
|---|---|---|---|---|---|
| **1 · ATS boards** | The company's own applicant-tracking system, read through its public postings API | Only the companies you point at — but *every* role at each | Minutes | Usually yes, full text | Greenhouse, Lever, Ashby, Workday, SmartRecruiters |
| **2 · Curated lists** | Human-maintained lists of roles for one audience (interns, new grads) | Broad across companies, narrow by audience | Hours–days (someone has to submit it) | No — just a link | SimplifyJobs, DereC4, Early Career Radar |
| **3 · Careers pages** | Any company careers page that embeds schema.org `JobPosting` JSON-LD (what Google Jobs indexes) | Anything with a careers site | Minutes | Often yes | Most custom career sites |
| **4 · Aggregators** | Boards that re-list from many ATSs | Very broad | Lags the source | Varies | LinkedIn, Indeed, Wellfound, YC |
| **5 · Social / threads** | Posts humans make about openings | Spotty but early | Instant | No | HN "Who is hiring", X/Twitter |

The product already drinks from tiers 1 and 2 (six source kinds). **The highest-leverage move is
to make tier 1 universal** — every major ATS, with the company universe seeded from tier 2 and a
watchlist — and to add tier 3 as the generic fallback for companies on a custom site. Tier 4 is
mostly off-limits (ToS) and redundant once tier 1 is wide. Tier 5 is a roadmap item.

---

## 2. The ATS catalog

The applicant-tracking market is concentrated: roughly a dozen systems host the overwhelming majority
of postings at companies a tech candidate cares about. Each has a different "door". This is the
catalog, with what we get through each door and where it stands in the app.

| ATS | Typical companies | Public postings door | Auth | JD text | Post date | In app |
|---|---|---|---|---|---|---|
| **Greenhouse** | Startups → large tech (Stripe, Databricks, Anthropic) | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | none | ✅ | ✅ `first_published` | ✅ have |
| **Lever** | Startups, mid-size | `GET api.lever.co/v0/postings/{site}?mode=json` | none | ✅ | ✅ `createdAt` | ✅ have |
| **Ashby** | AI/infra startups (OpenAI, Cursor, Linear) | `GET api.ashbyhq.com/posting-api/job-board/{org}` | none | ✅ | ✅ `publishedAt` | ✅ have |
| **Workday** | Most of the Fortune 500 (Salesforce, Nvidia, airlines, banks) | `POST {tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (JSON `{limit:20, offset, searchText}`), then `GET …{externalPath}` per job | none | ✅ (per-job call) | ~ `postedOn` is relative text ("Posted 3 Days Ago") | **✅ added here** |
| **SmartRecruiters** | Mid/large (Bosch, Visa, Ubisoft, many EU) | `GET api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=0`, then `…/postings/{postingId}` | none | ✅ (per-job call) | ✅ `releasedDate` | **✅ added here** |
| **Workable** | SMB, EU startups | `GET apply.workable.com/api/v1/widget/accounts/{subdomain}?details=true` | none | ✅ | ✅ `published_on` | **✅ added here** |
| **BambooHR** | SMB | `GET {sub}.bamboohr.com/careers/list`, then `…/careers/{id}/detail` | none | ✅ (per-job call) | ~ sometimes | **✅ added here** |
| **Recruitee** | EU startups | `GET {slug}.recruitee.com/api/offers/` | none | ✅ | ✅ `published_at` | **✅ added here** |
| **schema.org JSON-LD** | Any custom careers page (Google-Jobs-indexed) | `GET` the page; parse `<script type="application/ld+json">` `JobPosting` objects (incl. `@graph` and arrays) | none | ✅ `description` | ✅ `datePosted` | **✅ added here** (`careers_page`) |
| Oracle HCM / Taleo | Big enterprise (Oracle, banks) | `GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?…finder=findReqs;siteNumber={site}` (likely; verify per host) | none | ✅ | ✅ | roadmap |
| iCIMS | Enterprise (H&R Block, hospitals, retail) | HTML search pages / RSS; no clean public JSON | none | partial | ~ | roadmap (HTML) |
| SuccessFactors (SAP) | Enterprise | HTML + OData; inconsistent | none | partial | ~ | roadmap |
| Jobvite | Mid-size | HTML list page; API is keyed | key | — | — | skip |
| Teamtailor | EU | API is keyed | key | — | — | skip |
| Rippling / Gusto / Paylocity | SMB | HTML, embedded boards | none | partial | ~ | via `careers_page` if JSON-LD present |

Two things this table makes obvious:

1. **Workday is the single biggest gap** in the existing six kinds — it hosts most large employers,
   and the curated lists only catch the handful someone submits. Its listing API needs a POST, which
   is why `net.py` grows a `fetch_json_post`.
2. **JSON-LD is the universal fallback.** Any company that wants to appear in Google Jobs embeds a
   `JobPosting` object on each posting page and usually on the listing page. One generic fetcher
   covers the long tail of custom sites without per-ATS code.

### Per-source data quality

The scanner's funnel (`filters.passes`) needs `title`, `location`, `age_days`; the scorer needs a
`description`. Sources differ in what they hand over up front:

- **Full JD in the listing call:** Greenhouse (`content=true`), Lever, Ashby, Workable, Recruitee,
  JSON-LD. These skip the per-job fetch entirely.
- **JD needs a second call:** Workday, SmartRecruiters, BambooHR. The fetcher does the second call
  only for rows that survive `passes()` — never for the whole board — so a 2,000-posting Workday
  tenant costs ~20 listing pages plus one call per *matching* role.
- **No JD at all:** curated lists. `_ensure_description` fetches it at score time (already the case).
- **Relative dates:** Workday's `postedOn` is text ("Posted Today", "Posted 30+ Days Ago"). We parse
  it into `age_days`; "30+" becomes 31 so the age window still applies.

---

## 3. From "a list of companies" to "every posting": the company universe

A board fetcher needs a board token. Users won't type `tenant|wd5|External_Careers`. So the real
product question is: **how do we get from a company name or a careers URL to the right source,
automatically?**

### 3.1 Three ways companies enter the universe

1. **The user names them** — a watchlist. `config/watchlist.yaml` already does this for the CLI
   (groups of `{name, ats, board}`). In the app, the same idea is "Watch a company": paste a careers
   URL and we detect the rest.
2. **Curated lists name them for us.** Every row in SimplifyJobs/DereC4 carries a direct ATS link.
   Those links *are* board discoveries: a `boards.greenhouse.io/acme/jobs/123` link means Acme is on
   Greenhouse with token `acme`. Harvesting the companies behind the lists you already read turns
   "the roles someone submitted" into "every role at those companies." (Roadmap: a one-click
   "watch every company in this list".)
3. **Presets.** Lists of known boards for a level/audience, shipped with the app.

### 3.2 ATS auto-detect (`sources.detect_source`)

Given a URL (or anything the user pastes), detect the ATS and the board token from the URL shape,
and when the URL is a custom careers site, sniff the page for an embedded board:

| Input looks like | Detected |
|---|---|
| `boards.greenhouse.io/{token}` / `job-boards.greenhouse.io/{token}` / `…?for={token}` | `greenhouse`, board=`token` |
| `jobs.lever.co/{site}` | `lever`, board=`site` |
| `jobs.ashbyhq.com/{org}` | `ashby`, board=`org` |
| `{tenant}.{wdN}.myworkdayjobs.com/{locale?}/{site}` | `workday`, board=`tenant\|wdN\|site` |
| `jobs.smartrecruiters.com/{Company}` / `careers.smartrecruiters.com/{Company}` | `smartrecruiters`, board=`Company` |
| `apply.workable.com/{sub}` / `{sub}.workable.com` | `workable`, board=`sub` |
| `{sub}.bamboohr.com/careers` | `bamboohr`, board=`sub` |
| `{slug}.recruitee.com` | `recruitee`, board=`slug` |
| `github.com/{owner}/{repo}` | `listing_repo` |
| anything else | fetch the page once; look for an embedded Greenhouse/Lever/Ashby/Workday board (`boards-api.greenhouse.io/…?for=`, `jobs.lever.co/`, `api.ashbyhq.com/…/job-board/`, `myworkdayjobs.com/wday/cxs/`); else if the page has `JobPosting` JSON-LD → `careers_page` |

Detection never guesses silently: the UI shows "Detected: Greenhouse board `acme`" and lets the user
confirm before the source is saved. Unresolvable URLs are reported, not added.

### 3.3 Coverage, honestly

With the ten kinds in this branch, coverage of *tech-company postings* by ATS share is high — the
long tail of custom sites is caught by JSON-LD, and the remaining gaps are enterprise systems
(Oracle HCM, iCIMS, SuccessFactors) that mostly host roles the current level filters (internship/new
grad in tech) would drop anyway. The next most valuable adds, in order: Oracle HCM (banks, Oracle
itself), iCIMS (retail/health enterprise), HN "Who is hiring" (startups that post nowhere else).

---

## 4. The funnel (unchanged, now wider at the top)

Every row from every source goes through the same deterministic funnel before an LLM sees it:

```
fetch_source(src)            rows: {company, title, location, url, age_days, description?}
  → filters.passes(row)      title keywords · excluded titles/companies · level (boards only) · location · age
  → job_id(company,title,loc) exact dedup against jobs already in the store
  → same_position(row, tracked_pairs)   fuzzy dedup against anything drafted/applied/… (and now: imported)
  → insert; description saved if the source gave one
```

Two funnel notes that matter for wide sourcing:

- **Level gate applies to boards, not lists.** A company board lists every level; curated lists are
  already one level. `BOARD_KINDS` grows to include the new board kinds so "Senior Staff Engineer"
  from a Workday tenant is dropped before costing anything.
- **Age window is the budget.** `max_age_days` (default 14, the CLI uses 7) is what keeps a 3,000-row
  Workday tenant from flooding the inbox: only the last two weeks are considered, and the JD fetch
  happens after that cut.

### Etiquette

One request at a time per host; 30 s timeouts; the project User-Agent on every call; per-job
detail calls only for rows that pass the funnel; a source that errors is reported in the run
summary and skipped, never retried in a tight loop. Scans are user-triggered or on the auto-run
interval (hours), never continuous.

---

## 5. Tracker import: never scan what you've already done

People arrive with a history — a spreadsheet, a Notion export, a Simplify/Huntr CSV, a markdown
table in their notes. Without it, the first scan re-surfaces roles they applied to months ago, and
dedup only knows what happened *inside* the app.

### 5.1 What it does

**Import a tracker file → those applications become jobs in the Tracker with their real status →
the scanner skips them forever.**

That last part is free: `scan()` already dedups against `store.tracked_pairs()` — every job whose
status is drafting/drafted/applied/interviewing/offer/rejected. Imported applications land with one
of those statuses, so they join the dedup set with no new code path in the scanner. The fuzzy
`same_position` match (normalized company + role-token overlap) is what makes "Software Engineering
Intern, iOS" in your sheet match "Software Engineer Intern - iOS" on the board.

### 5.2 Formats and column mapping

Accepted: **CSV, TSV, Markdown pipe tables, plain lines** (`Company — Role — Status`). Spreadsheets
export to CSV in one click, so `.xlsx` isn't parsed directly.

Headers are mapped by synonym, case-insensitively, so most trackers import with no setup:

| Field | Header synonyms |
|---|---|
| company | company, employer, org, organization, firm |
| title | role, title, position, job, job title, posting |
| location | location, city, where |
| url | url, link, posting link, apply, application link |
| status | status, stage, state, outcome, result |
| applied_at | applied, date applied, applied on, date, submitted, sent |

Statuses are normalized to the app's vocabulary; anything unrecognized defaults to `applied`
(the safe assumption for a row in an application tracker):

| Tracker says | Becomes |
|---|---|
| applied, submitted, sent, done, ✅ | `applied` |
| interview, interviewing, phone screen, OA, assessment, onsite, final round, recruiter call | `interviewing` |
| offer, accepted, signed | `offer` |
| rejected, declined, no, closed, ghosted, ❌ | `rejected` |
| withdrawn, not applying, skip | `archived` |

Dates accept ISO, `MM/DD/YYYY`, `DD.MM.YYYY`, and `Oct 3, 2026`; unparseable dates are left empty
rather than guessed.

### 5.3 Preview, then apply — nothing silent

Import is two steps. The file is parsed and matched **without writing anything**, and the user sees
exactly what will happen per row:

- **new** — not in the app; will be added to the Tracker with that status.
- **update** — fuzzy-matches a job already in the app; its status will move to the imported one
  (never backwards from a later stage, e.g. an in-app `offer` is not downgraded to `applied`).
- **same** — already in the app at that status; nothing to do.

Then one click applies it. Imported jobs carry `source = "tracker import"` so they're always
distinguishable from scanned ones.

### 5.4 Privacy

The file never leaves the machine and never goes to the model: parsing is plain code, matching is
`same_position`. No LLM call is involved in an import.

---

## 6. What this branch ships vs. the roadmap

**Shipped here**

- `net.fetch_json_post` (Workday needs it).
- Five new board kinds: `workday`, `smartrecruiters`, `workable`, `bamboohr`, `recruitee`.
- `careers_page`: the generic schema.org `JobPosting` JSON-LD fetcher.
- `sources.detect_source(text)`: ATS auto-detect from a URL, with page sniffing for embedded boards.
- "Watch a company" in Search & sources: paste any careers URL → detected source → confirm.
- `BOARD_KINDS` extended so the level gate covers the new boards.
- Tracker import: `jobhunt/tracker.py` (parse · normalize · match), `POST /api/tracker/import`
  (preview) and `POST /api/tracker/import/apply`, and an **Import tracker** flow on the Tracker page.
- Offline tests for every fetcher's parser, the detector, and the import round-trip
  (import → `tracked_pairs` → `scan` skips the role).

**Roadmap, ranked by roles-per-hour-of-work**

1. **Harvest companies from lists** — "watch every company in SimplifyJobs" by turning each row's
   ATS link into a board source (the detector already does the per-link work).
2. **Oracle HCM** fetcher (banks, Oracle, large enterprise).
3. **Tracker import from Gmail** — the same import flow fed by "Thanks for applying" emails; pairs
   with the planned review-gated email status tracking.
4. **HN "Who is hiring"** (monthly thread via the HN Algolia API) — early-stage roles that are
   nowhere else.
5. **iCIMS / SuccessFactors** HTML fetchers.
6. **RSS** as a generic kind for the few boards that publish feeds.
7. Per-source stats in the run summary (rows / passed / new) so users can see which sources earn
   their keep and prune the rest.

**Deliberately not doing**

- LinkedIn, Indeed, Glassdoor, Wellfound, YC Work at a Startup: behind auth or against ToS. Also
  redundant once tier 1 is wide, since they re-list from the same ATSs.
- Keyed APIs (Jobvite, Teamtailor) that would require each user to get a vendor API key.
- Any "auto-apply". Sourcing wider changes nothing about the review gate: you still submit.
