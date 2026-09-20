# job-hunt-agent

A reproducible, multi-agent recruiting pipeline for Claude Code. It watches job-listing
sources, scores each new role against your profile, drafts a tailored application package,
and **fills the form up to the submit button** so you review and submit yourself. It never
submits, creates accounts, or types passwords on your behalf.

## The agents

| Agent | Job |
|-------|-----|
| **Scout** | Poll listing sources, diff against seen + already-applied roles, apply hard filters, queue the new ones |
| **Matcher** | Score each role 0-100 on a transparent rubric, pick a resume category, flag ineligible roles |
| **Applier** | Resolve the real ATS link, tailor the resume, draft answers, fill to the submit button, stop for review |
| **Tracker** | Maintain your board, detect recruiter replies via email, surface cross-application patterns |

Orchestrated by the `/recruit` skill. A companion `/outreach` skill drafts a post-application
cold email to a real contact (unsent Gmail draft only).

## Sources it can watch

- **GitHub markdown listing repos** (e.g. DereC4-style tables) — links auto-resolved, incl. a
  deterministic decoder for `zapply.jobs` tracking slugs.
- **GitHub HTML listing repos** (e.g. SimplifyJobs) — direct ATS links extracted, no redirect.
- **Web listing pages** parsed from their public HTML (e.g. Early Career Radar), using the
  source's own role classification. Public pages only, never an API.

All configured in `config/preferences.yaml`.

## How scoring works

Each role gets a 0-100 fit score from a weighted rubric (`config/scoring.yaml`):
role-type (30) + tech-stack overlap (25) + eligibility & logistics (20) + level fit (15) +
domain/strengths (10). Every score returns its sub-scores and a one-line reason. Hard gates
(citizenship-required, grad-only, wrong term, impossible location, defense) flag a role
`INELIGIBLE` regardless of score. The threshold decides what happens: `>=70` auto-draft,
`55-69` review manually, below that dropped. Tune it in the config after your first run.

## Quick start

```bash
git clone https://github.com/yih0nk/job-hunt-agent
cd job-hunt-agent
pip install -r requirements.txt

cp config/profile.example.yaml         config/profile.yaml
cp config/preferences.example.yaml     config/preferences.yaml
cp config/learned-answers.example.yaml config/learned-answers.yaml
# edit those three with your details + the sources you want watched (all gitignored)

python3 bin/poll-repo.py --config config/preferences.yaml   # smoke-test the Scout
```
Then run `/recruit` in Claude Code.

## Make it yours

1. **Fill the three configs.** `profile.yaml` (identity, work auth, EEO, resume paths,
   tracker paths), `preferences.yaml` (sources, role types, locations, hard-no companies),
   `learned-answers.yaml` (reusable answers to repeated application questions).
2. **Point at your resume system.** `profile.resume.work_dir` holds a base LaTeX resume plus
   per-category variant folders. Copy `RESUME-TAILORING.template.md` to your own spec and set
   `profile.resume.tailoring_spec` to it — the Applier follows whatever that spec says.
3. **Set your trackers.** `tracker_path` (what you've actually submitted) and
   `packages_tracker_path` (tailored packages). Both are read for dedup, so no position is
   ever surfaced, re-drafted, or re-contacted twice.
4. **Optional autonomy.** Schedule `/recruit` (e.g. a local scheduled task) to scan, score,
   and draft on a cadence so packages are waiting for your review.

## Time-window scans

```bash
python3 bin/poll-repo.py --max-age-days 1 --ignore-seen   # everything posted in the past day
python3 bin/poll-repo.py --max-age-days 7 --ignore-seen   # past week
```
`--ignore-seen` is query mode: it does not record results or dedup against prior runs.

## Privacy model

`config/*.yaml` (your real profile, preferences, learned answers), `state/`, and
`applications/` are **gitignored**. Only the `*.example.yaml` templates and the agent/skill
code are public. Your resume, answers, and drafted packages never leave your machine.

## What it will not do

Submit applications · create accounts · type or set passwords · solve CAPTCHAs ·
fabricate experience · apply to defense companies · check "Applied" without your
confirmation. These are handed off to you by design — a review gate produces better
applications and keeps you out of ATS bot filters.

## Layout

```
agents/     scout · matcher · applier · tracker
skills/     recruit orchestrator · outreach
bin/        poll-repo.py (parse + diff + dedup) · resolve-link.py (aggregator -> real ATS)
config/     scoring.yaml + *.example.yaml templates
state/      runtime (gitignored)
RESUME-TAILORING.template.md   copy to your own tailoring spec
```

## License

MIT — see [LICENSE](LICENSE).
