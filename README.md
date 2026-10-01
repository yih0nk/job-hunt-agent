# job-hunt-agent

Finds new roles, scores each one against your background, and drafts a tailored one-page
resume plus application answers. **You review and submit every application yourself.** It never
submits, creates accounts, types passwords, or invents experience.

It comes in two forms that share the same scanner and rules:

- **Desktop app** (`jobhunt/` + `web/` + `desktop/`): a local app with a UI, for anyone. Bring
  your own Anthropic API key, import your resume, and pick where to look.
- **Claude Code mode** (`agents/` + `skills/`): the original multi-agent pipeline, driven by
  `/recruit` inside Claude Code, for people who want to wire it into their own notes and scripts.

## Desktop app

### What it does

| Step | What happens |
|------|--------------|
| **Scan** | Polls your sources, drops what's obviously out (wrong title, level, location, age, excluded company), and dedups against roles you've already drafted or applied to. |
| **Score** | Fetches the job description and has Claude score fit 0-100 on five weighted parts (role fit, skills, eligibility, level, preferences), each with a one-line reason. Hard gates (work authorization, level, term, location, your dealbreakers) mark a role ineligible. |
| **Draft** | Tailors a one-page resume from your **experience bank**. Every bullet must trace back to a real bullet you wrote; untraceable bullets are discarded in code, not just by prompt. Drafts answers to the posting's real questions where the ATS publishes them (Greenhouse), reusing your saved answers word for word. |
| **Review** | You edit bullets, re-render the PDF, copy answers, open the posting, and submit. Then you mark it *Applied* and track it through interviews on the board. |

Sources: GitHub listing repos (SimplifyJobs, DereC4 and similar, both markdown and HTML tables),
pending-submission issues on those repos, any company's **Greenhouse / Lever / Ashby** board, and
Early Career Radar. You can also paste in any role by hand.

### Run it (from source)

```bash
git clone https://github.com/yih0nk/job-hunt-agent && cd job-hunt-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm --prefix web install && npm --prefix web run build
.venv/bin/python -m jobhunt          # opens http://127.0.0.1:<port>/ in your browser
```

Or as a desktop window:

```bash
npm --prefix desktop install
npm --prefix desktop start
```

The first launch walks you through three steps: API key → resume import → search and sources.

### Build the installable app

```bash
.venv/bin/pip install -r requirements-dev.txt
npm --prefix desktop run dist        # web build → PyInstaller backend → electron-builder
```

This produces a `.dmg` (macOS), NSIS installer (Windows), or AppImage (Linux) in
`desktop/dist/`. The Python backend is bundled, so users don't need Python installed. Builds are
unsigned; signing and notarization are up to you.

### Cost

Scoring one role is one Claude call over the job description plus your profile. The profile is
prompt-cached, so a scan that scores many roles pays for it roughly once. Drafting a package takes
two larger calls. The default model is Claude Opus 5. Settings lets you switch to Sonnet 5 or
Haiku 4.5 and lower the effort to spend less.

### Where your data lives

Everything (profile, jobs, packages, and your API key) is stored in one local folder:
`~/Library/Application Support/JobHuntAgent` on macOS, `%APPDATA%\JobHuntAgent` on Windows,
`~/.local/share/job-hunt-agent` on Linux, or `$JOBHUNT_HOME` if set. The only thing that leaves
your machine is the text sent to Claude for scoring and drafting. The local server binds to
127.0.0.1 and requires a per-launch token, so other websites in your browser can't drive it.

### Develop

```bash
JOBHUNT_TOKEN=dev .venv/bin/python -m jobhunt --port 8765 --no-browser   # API
npm --prefix web run dev                                                   # UI with hot reload, proxies /api
.venv/bin/python -m pytest tests                                           # offline tests (no network, no API spend)
```

Layout:

```
jobhunt/    backend: sources · filters · jd (link resolve + JD/question fetch) · llm · pipeline · resume (Typst) · server
web/        React + Vite UI
desktop/    Electron shell + PyInstaller spec for the bundled backend
tests/      offline tests; the Claude path runs against a mocked HTTP transport
```

## Claude Code mode

The original pipeline: four agents orchestrated by the `/recruit` skill.

| Agent | Job |
|-------|-----|
| **Scout** | Poll listing sources, diff against seen and already-applied roles, apply hard filters, queue the new ones |
| **Matcher** | Score each role 0-100 on the rubric in `config/scoring.yaml`, pick a resume category, flag ineligible roles |
| **Applier** | Resolve the real ATS link, tailor the resume via your tailoring spec, draft answers, stop for review |
| **Tracker** | Maintain your board, detect recruiter replies via email, surface cross-application patterns |

A companion `/outreach` skill drafts a post-application cold email to a real contact (as an unsent Gmail draft only).

```bash
pip install -r requirements.txt
cp config/profile.example.yaml         config/profile.yaml
cp config/preferences.example.yaml     config/preferences.yaml
cp config/learned-answers.example.yaml config/learned-answers.yaml
# fill those in (all gitignored), then:
python3 bin/poll-repo.py --config config/preferences.yaml   # smoke-test the Scout
```

Then run `/recruit` in Claude Code. Point `profile.resume.tailoring_spec` at your own copy of
`RESUME-TAILORING.template.md`, and set `tracker_path` / `packages_tracker_path` so dedup
covers everything you've already submitted or drafted.

Time-window scans: `python3 bin/poll-repo.py --max-age-days 7 --ignore-seen` shows everything
posted in the past week without recording it.

## What it will not do

Submit applications · create accounts · type or set passwords · solve CAPTCHAs ·
fabricate experience · mark anything "Applied" without you. A human review gate produces better
applications and keeps you out of ATS bot filters.

## License

MIT — see [LICENSE](LICENSE).
