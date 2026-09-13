---
name: recruit
description: Run the recruiting pipeline end to end (scout -> match -> apply -> track) and produce the daily report. Use when the user says "check for new roles", "run recruit", or on the daily schedule.
---

# /recruit — recruiting pipeline orchestrator

Coordinates four agents. State lives in `state/`; personal config in `config/*.yaml`
(gitignored). Nothing is ever submitted automatically — the Applier fills to the submit
button and stops for review.

## Flow
1. **Scout** — `agents/scout.md`. Poll sources, diff, filter, queue new roles.
2. **Matcher** — `agents/matcher.md`. Score each new role 0-100, pick a resume category,
   flag ineligible. Bucket by `config/scoring.yaml` thresholds.
3. **Applier** — `agents/applier.md`. For `auto_draft` roles: resolve link, tailor resume
   (via the standing tailoring spec), draft answers, fill to the submit button, screenshot,
   stage under `applications/`. Hand off for accounts/CAPTCHAs.
4. **Tracker** — `agents/tracker.md`. Update the board, detect recruiter replies, update
   pattern stats.

## Output — the daily report
```
Recruiting — <date>
  Scanned N · passed filters M · NEW k
  Auto-drafted (awaiting your review): <company/role @ score> ...
  Review manually (55-69): ...
  Ineligible (gated): <company/role — gate> ...
  Recruiter activity: <replies / OA deadlines>
  Patterns: <most common ATS / question / etc.>
  Action needed from you: <accounts to create / packages to review + submit>
```

## Modes
- `/recruit` — full run + report.
- `/recruit scan` — Scout + Matcher only (no drafting).
- `/recruit apply <company>` — draft a package for one specific queued role.

## Invariants
- Never submit an application. Never create accounts or type passwords. Never solve CAPTCHAs.
- Never check "Applied" in the tracker without explicit user confirmation.
- Only credit real experience; delegate all resume tailoring to the spec.
