# job-hunt-agent

A reproducible, multi-agent recruiting pipeline for Claude Code. It watches job-listing
repos (and optional company boards), scores each new role against your profile, drafts a
tailored application package, and **fills the form up to the submit button** so you review
and submit yourself. It never submits, creates accounts, or types passwords on your behalf.

## The agents

| Agent | Job |
|-------|-----|
| **Scout** | Poll listing repos, diff against seen roles, apply hard filters, queue the new ones |
| **Matcher** | Score each role 0-100 on a transparent rubric, pick a resume category, flag ineligible roles |
| **Applier** | Resolve the real ATS link, tailor the resume, draft answers, fill to the submit button, stop for review |
| **Tracker** | Maintain your board, detect recruiter replies via email, surface cross-application patterns |

Orchestrated by the `/recruit` skill.

## How scoring works

Each role gets a 0-100 fit score from a weighted rubric (`config/scoring.yaml`):
role-type (30) + tech-stack overlap (25) + eligibility & logistics (20) + level fit (15) +
domain/strengths (10). Every score returns its sub-scores and a one-line reason. Hard gates
(citizenship-required, grad-only, wrong term, impossible location) flag a role `INELIGIBLE`
regardless of score. The threshold decides what happens: `>=70` auto-draft, `55-69` review
manually, `<70`... tune it in the config after your first run.

## Quick start

```bash
git clone https://github.com/yih0nk/job-hunt-agent
cd job-hunt-agent
pip install -r requirements.txt

cp config/profile.example.yaml       config/profile.yaml
cp config/preferences.example.yaml   config/preferences.yaml
cp config/learned-answers.example.yaml config/learned-answers.yaml
# edit those three with your details + the repos you want watched (all gitignored)

python3 bin/poll-repo.py --config config/preferences.yaml   # smoke-test the Scout
```
Then run `/recruit` in Claude Code.

## Privacy model

`config/*.yaml` (your real profile, preferences, learned answers), `state/`, and
`applications/` are **gitignored**. Only the `*.example.yaml` templates and the agent/skill
code are public. Your resume, answers, and drafted packages never leave your machine.

## What it will not do

Submit applications · create accounts · type or set passwords · solve CAPTCHAs ·
fabricate experience · check "Applied" without your confirmation. These are handed off to
you by design — a review gate produces better applications and keeps you out of ATS bot
filters.

## Layout

```
agents/     scout · matcher · applier · tracker
skills/     recruit orchestrator
bin/        poll-repo.py (parse + diff) · resolve-link.py (aggregator -> real ATS)
config/     scoring.yaml + *.example.yaml templates
state/      runtime (gitignored)
```
