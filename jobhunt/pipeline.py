"""Scout -> Matcher -> Applier, as plain functions over the Store.

Runs in a background thread; progress is exposed through `Progress` for the UI to poll.
Nothing here submits anything. A package is a PDF + drafted answers for human review.
"""
from __future__ import annotations

import queue
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import jd, llm
from .filters import job_id, passes, same_position
from .models import TailoredResume
from .paths import packages_dir
from .resume import build_data, page_count, render_pdf, wrapped_bullets
from .sources import fetch_source
from .store import Store


@dataclass
class Progress:
    running: bool = False
    stage: str = ""
    done: int = 0
    total: int = 0
    message: str = ""
    errors: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def as_dict(self):
        return {"running": self.running, "stage": self.stage, "done": self.done,
                "total": self.total, "message": self.message, "errors": self.errors[-10:],
                "summary": self.summary}


progress = Progress()
_run_lock = threading.Lock()


# --- Scout --------------------------------------------------------------------------

def scan(store: Store) -> dict:
    prefs = store.preferences()
    tracked = store.tracked_pairs()
    counts = {"scanned": 0, "new": 0, "dropped": {}, "already_tracked": 0, "source_errors": []}
    sources = [s for s in prefs.sources if s.enabled]
    progress.stage, progress.total, progress.done = "scan", len(sources), 0
    for src in sources:
        progress.message = f"Scanning {src.name or src.repo or src.board}"
        try:
            rows = fetch_source(src)
        except Exception as e:
            counts["source_errors"].append(f"{src.name or src.kind}: {e}")
            progress.errors.append(f"Source failed: {src.name or src.kind}: {e}")
            progress.done += 1
            continue
        for row in rows:
            counts["scanned"] += 1
            ok, why = passes(row, prefs)
            if not ok:
                counts["dropped"][why] = counts["dropped"].get(why, 0) + 1
                continue
            jid = job_id(row["company"], row["title"], row.get("location", ""))
            if store.has_job(jid):
                continue
            if any(same_position((row["company"], row["title"]), t) for t in tracked):
                counts["already_tracked"] += 1
                continue
            store.insert_job({**row, "id": jid})
            if row.get("description"):
                store.update_job(jid, description=row["description"], resolved_url=row.get("url", ""))
            counts["new"] += 1
        progress.done += 1
    return counts


# --- Matcher ------------------------------------------------------------------------

def _ensure_description(store: Store, job: dict) -> dict:
    if len((job.get("description") or "").strip()) >= jd.MIN_JD_CHARS:
        return job
    url = jd.resolve(job.get("url", ""))
    posting = jd.fetch_posting(url, job.get("company", ""))
    store.update_job(job["id"], resolved_url=url, description=posting.get("text", ""))
    job.update(resolved_url=url, description=posting.get("text", ""))
    return job


def score_one(store: Store, job_id_: str) -> dict:
    llm.current_job.set(job_id_)
    prefs, profile, settings = store.preferences(), store.profile(), store.settings()
    job = _ensure_description(store, store.job(job_id_))
    fit = llm.score(settings, profile, prefs, job)
    no_jd = len((job.get("description") or "").strip()) < jd.MIN_JD_CHARS
    total = llm.weighted_total(fit, prefs)
    if fit.ineligible:
        status = "ineligible"
    elif total >= prefs.thresholds.review:
        status = "review"
    else:
        status = "scored"
    store.update_job(job["id"], score=total, score_detail={**fit.model_dump(), "no_jd": no_jd}, status=status)
    return store.job(job["id"])


def backfill_descriptions(store: Store) -> dict:
    """Refetch job descriptions that came back empty (JS-only pages, unknown board tokens).
    Free: no AI calls. Returns how many were fixed and which scored roles now deserve a re-score."""
    todo = [j for j in store.jobs(limit=2000)
            if j["status"] != "archived" and len((j.get("description") or "").strip()) < jd.MIN_JD_CHARS]
    fixed, rescore = 0, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for j, posting in zip(todo, pool.map(lambda j: jd.fetch_posting(jd.resolve(j["resolved_url"] or j["url"]), j["company"]), todo)):
            if posting.get("text"):
                store.update_job(j["id"], description=posting["text"], resolved_url=posting["url"])
                fixed += 1
                if j.get("score_detail"):
                    # That score was made from the title alone; say so on the role.
                    store.update_job(j["id"], score_detail={**j["score_detail"], "no_jd": True})
                    rescore.append(j["id"])
    return {"checked": len(todo), "fixed": fixed, "rescore": rescore}


class Budget:
    """Stops a run's paid steps (scoring, auto-drafting) once the spend cap is reached.
    Per-run counts spend since this run started; per-day counts since local midnight.
    A cap of 0 means no cap. Roles left unscored simply wait for the next run."""

    def __init__(self, store: Store, settings, run_started: float):
        self.store, self.cap = store, float(settings.spend_cap_usd or 0)
        self.per = settings.spend_cap_per
        self.floor = run_started if self.per == "run" else _local_midnight()
        self.hit = False

    def ok(self) -> bool:
        if self.cap <= 0:
            return True
        if not self.hit and self.store.spend_since(self.floor) >= self.cap:
            self.hit = True
        return not self.hit

    @property
    def label(self) -> str:
        return f"${self.cap:.2f} per {self.per}"


def _local_midnight(now: float | None = None) -> float:
    now = time.time() if now is None else now
    return time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))


def score_new(store: Store, limit: int = 60, budget: Budget | None = None, workers: int = 4) -> dict:
    pending = store.jobs(["new"], limit=limit)
    progress.stage, progress.total, progress.done = "score", len(pending), 0
    counts = {"scored": 0, "review": 0, "ineligible": 0, "failed": 0, "capped": 0}

    def work(j):
        if budget and not budget.ok():
            counts["capped"] += 1          # stays 'new'; scored on a later run
            progress.done += 1
            return
        progress.message = f"Scoring {j['company']} — {j['title']}"
        try:
            out = score_one(store, j["id"])
            counts["scored"] += 1
            if out["status"] in counts:
                counts[out["status"]] += 1
        except llm.LLMError as e:
            counts["failed"] += 1
            progress.errors.append(f"{j['company']}: {e}")
        except Exception as e:
            counts["failed"] += 1
            progress.errors.append(f"{j['company']}: {e}")
            traceback.print_exc()
        finally:
            progress.done += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, pending))
    return counts


# --- Applier ------------------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def draft(store: Store, job_id_: str) -> dict:
    """Tailor a resume and draft answers. Stops at a review-ready package."""
    llm.current_job.set(job_id_)
    prefs, profile, settings = store.preferences(), store.profile(), store.settings()
    if not profile.experience:
        raise llm.LLMError("Your experience bank is empty. Import a resume in Profile first.")
    job = _ensure_description(store, store.job(job_id_))
    url = job.get("resolved_url") or jd.resolve(job.get("url", ""))
    posting = jd.fetch_posting(url, job.get("company", "")) if url else {"questions": []}

    tailored = llm.tailor(settings, profile, prefs, job)
    out_dir = packages_dir() / f"{_slug(job['company'])}-{_slug(job['title'])}-{job['id'][:6]}"
    name = _slug(profile.name or "resume").replace("-", "_") or "resume"
    pdf, pages = fit_page(profile, tailored, out_dir / f"{name}_resume.pdf")
    pdf, pages = one_line_bullets(settings, profile, prefs, tailored, pdf, pages)

    ans = llm.answers(settings, profile, prefs, job, posting.get("questions"))
    package = {
        "created": time.time(),
        "resolved_url": url,
        "pdf": str(pdf),
        "pages": pages,
        "tailored": tailored.model_dump(),
        "answers": ans.model_dump(),
        "questions_source": "ats" if posting.get("questions") else "generic",
    }
    store.update_job(job["id"], package=package, status="drafted", resolved_url=url)
    return store.job(job["id"])


def outreach(store: Store, job_id_: str) -> dict:
    """Find real contacts for a role and draft messages. Never sends anything."""
    llm.current_job.set(job_id_)
    job = _ensure_description(store, store.job(job_id_))
    plan = llm.outreach(store.settings(), store.profile(), store.preferences(), job,
                        applied=job["status"] in ("applied", "interviewing", "offer"))
    store.update_job(job_id_, outreach={**plan.model_dump(), "created": time.time()})
    return store.job(job_id_)


MAX_TRIM = 20
MAX_FILL_TRIES = 40


def _order(t: TailoredResume, profile) -> None:
    """Work, research, and leadership entries go back to the bank's (chronological) order;
    projects keep the tailor's relevance order."""
    rank = {e.id: i for i, e in enumerate(profile.experience)}
    kind = {e.id: e.kind for e in profile.experience}
    projects = [te for te in t.entries if kind.get(te.entry_id) == "project"]
    others = sorted((te for te in t.entries if kind.get(te.entry_id) != "project"), key=lambda te: rank.get(te.entry_id, 1e9))
    t.entries = others + projects


def _fill_candidates(t: TailoredResume, profile) -> list[tuple[str, str, str]]:
    """Bank bullets the resume doesn't use yet, as (entry_id, bullet_id, text): first the
    unused bullets of entries already on the page, then whole entries that were left out."""
    used = {i.strip() for te in t.entries for ids in te.source_bullet_ids for i in ids.split(",")}
    present = {te.entry_id for te in t.entries}
    on_page = [_words(b) for te in t.entries for b in te.bullets]
    first, later = [], []
    for e in profile.experience:
        for b in e.bullets:
            # Skip lines already on the page in reworded form, even if the tailor cited a
            # different source id for them.
            if b.id in used or not b.text.strip() or any(_similar(_words(b.text), w) for w in on_page):
                continue
            (first if e.id in present else later).append((e.id, b.id, b.text))
    return first + later


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$%+.,]+", text.replace("**", "").lower()) if len(w) > 2}


def _similar(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.6


def _dedupe(t: TailoredResume) -> None:
    """Drop a bullet that repeats an earlier one (e.g. a profile line added back next to its
    own reworded version). The first, usually tailored, version stays."""
    seen: list[set[str]] = []
    for te in t.entries:
        keep_b, keep_ids = [], []
        for b, ids in zip(te.bullets, te.source_bullet_ids):
            w = _words(b)
            if any(_similar(w, x) for x in seen):
                continue
            seen.append(w)
            keep_b.append(b)
            keep_ids.append(ids)
        te.bullets, te.source_bullet_ids = keep_b, keep_ids
    t.entries = [te for te in t.entries if te.bullets]


def _add(t: TailoredResume, entry_id: str, bullet_id: str, text: str) -> None:
    from .models import TailoredEntry
    for te in t.entries:
        if te.entry_id == entry_id:
            te.bullets.append(text)
            te.source_bullet_ids.append(bullet_id)
            return
    t.entries.append(TailoredEntry(entry_id=entry_id, bullets=[text], source_bullet_ids=[bullet_id]))


def fit_page(profile, t: TailoredResume, pdf: Path) -> tuple[Path, int]:
    """Make the tailored resume exactly one full page, deterministically and for free.
    Fill: add back unused profile bullets (verbatim) while they still fit. Trim: if the
    tailor overshot, drop the least relevant lines. Every added line is a real profile line,
    so the no-fabrication guarantee holds."""
    _dedupe(t)
    _order(t, profile)
    pdf = render_pdf(build_data(profile, t), pdf, spread=False)
    pages = page_count(pdf)
    added, misses = [], 0
    if pages == 1:
        for entry_id, bullet_id, text in _fill_candidates(t, profile)[:MAX_FILL_TRIES]:
            trial = t.model_copy(deep=True)
            _add(trial, entry_id, bullet_id, text)
            _order(trial, profile)
            render_pdf(build_data(profile, trial), pdf, spread=False)
            if page_count(pdf) == 1:
                t.entries = trial.entries
                added.append(entry_id)
                misses = 0
            else:
                misses += 1
                if misses >= 3:   # three long lines in a row won't fit: the page is full
                    break
        pages = 1
    trimmed = 0
    while pages > 1 and trimmed < MAX_TRIM and _trim_one(t):
        trimmed += 1
        pdf = render_pdf(build_data(profile, t), pdf, spread=False)
        pages = page_count(pdf)
    pdf = render_pdf(build_data(profile, t), pdf)   # final render spreads spacing to fill the page
    if added:
        names = {e.id: (e.org if e.kind != "project" else e.title) for e in profile.experience}
        back = {names.get(i, "") for i in added}
        # The tailor's "left out X" notes are stale once X is back on the page.
        t.notes = [n for n in t.notes if not any(b and b.lower() in n.lower() for b in back)]
        t.notes.append(f"Added back {len(added)} line{'s' if len(added) > 1 else ''} from your profile to fill the page.")
    if trimmed:
        t.notes.append(f"Cut {trimmed} lower-priority bullet{'s' if trimmed > 1 else ''} to fit one page. "
                       "Restore any under Edit.")
    return pdf, pages


def one_line_bullets(settings, profile, prefs, t: TailoredResume, pdf: Path, pages: int) -> tuple[Path, int]:
    """House style: every bullet on one line. Measure each bullet in the real layout; send
    the ones that wrap back to be reworded (never shrink the font), then re-fit the page."""
    for _ in range(2):
        wrapped = wrapped_bullets(build_data(profile, t))
        if not wrapped:
            break
        items, where = [], {}
        for key in wrapped:
            entry_id, i = key.rsplit(":", 1)
            for te in t.entries:
                if te.entry_id == entry_id and int(i) < len(te.bullets):
                    text = te.bullets[int(i)]
                    plain = len(text.replace("**", ""))
                    items.append({"key": key, "text": text, "max_chars": max(60, min(plain - 8, 125))})
                    where[key] = (te, int(i))
        try:
            new = llm.shorten(settings, profile, prefs, items)
        except llm.LLMError:
            break
        for key, text in new.items():
            if key in where and text.strip():
                te, i = where[key]
                te.bullets[i] = text.strip()
        pdf, pages = fit_page(profile, t, pdf)
    left = wrapped_bullets(build_data(profile, t))
    if left:
        t.notes.append(f"{len(left)} bullet{'s' if len(left) > 1 else ''} still run two lines. Tighten under Edit if you like.")
    return pdf, pages


def refill(store: Store, job_id_: str) -> dict:
    """Re-fit an existing package to one full page without another Claude call."""
    job = store.job(job_id_)
    pkg = job["package"]
    t = TailoredResume.model_validate(pkg["tailored"])
    pdf, pages = fit_page(store.profile(), t, Path(pkg["pdf"]))
    pkg.update(tailored=t.model_dump(), pages=pages)
    store.update_job(job_id_, package=pkg)
    return store.job(job_id_)


def _trim_one(t: TailoredResume) -> bool:
    """Drop the least relevant line: entries are ordered most-relevant first, so cut from
    the end. Take the last bullet of the last multi-bullet entry; if every entry is down
    to one bullet, drop the last entry. Returns False when nothing more can go."""
    for e in reversed(t.entries):
        if len(e.bullets) > 1:
            e.bullets.pop()
            if len(e.source_bullet_ids) > len(e.bullets):
                e.source_bullet_ids.pop()
            return True
    if len(t.entries) > 1:
        t.entries.pop()
        return True
    return False


def rerender(store: Store, job_id_: str, tailored: dict) -> dict:
    """User edited the tailored resume in the UI: re-render the PDF from their edits."""
    job = store.job(job_id_)
    pkg = job["package"]
    t = TailoredResume.model_validate(tailored)
    pdf = render_pdf(build_data(store.profile(), t), Path(pkg["pdf"]))
    pkg.update(tailored=t.model_dump(), pages=page_count(pdf))
    store.update_job(job_id_, package=pkg)
    return store.job(job_id_)


# --- Background drafting (review mode hands roles off and moves on) ------------------

_drafts: "queue.Queue[tuple[str, str]]" = queue.Queue()
_draft_worker: threading.Thread | None = None


def _draft_loop(store: Store) -> None:
    while True:
        jid, prev = _drafts.get()
        try:
            draft(store, jid)
        except Exception as e:
            store.update_job(jid, status=prev, last_error=str(e)[:500])
        finally:
            _drafts.task_done()


def queue_draft(store: Store, jid: str) -> dict:
    """Mark a role as drafting and draft it on a single background worker."""
    global _draft_worker
    job = store.job(jid)
    prev = job["status"] if job["status"] != "drafting" else "review"
    store.update_job(jid, status="drafting", last_error="")
    if _draft_worker is None or not _draft_worker.is_alive():
        _draft_worker = threading.Thread(target=_draft_loop, args=(store,), daemon=True)
        _draft_worker.start()
    _drafts.put((jid, prev))
    return store.job(jid)


def recover_interrupted(store: Store) -> None:
    """Roles left 'drafting' when the app quit go back to the inbox with a note."""
    for j in store.jobs(["drafting"]):
        store.update_job(j["id"], status="review", last_error="Drafting was interrupted. Try again.")


# --- Full run -----------------------------------------------------------------------

def run(store: Store, do_scan: bool = True, do_score: bool = True) -> None:
    if not _run_lock.acquire(blocking=False):
        return
    try:
        progress.running, progress.errors, progress.summary = True, [], {}
        started = time.time()
        run_id = store.start_run()
        budget = Budget(store, store.settings(), started)
        summary: dict = {}
        if do_scan:
            summary["scan"] = scan(store)
        if do_score:
            summary["score"] = score_new(store, budget=budget)
            prefs = store.preferences()
            if prefs.auto_draft:
                ready = [j for j in store.jobs(["review"]) if (j["score"] or 0) >= prefs.thresholds.auto_draft]
                progress.stage, progress.total, progress.done = "draft", len(ready), 0
                drafted = 0
                for j in ready:
                    if not budget.ok():
                        break
                    progress.message = f"Drafting {j['company']} — {j['title']}"
                    try:
                        draft(store, j["id"])
                        drafted += 1
                    except Exception as e:
                        progress.errors.append(f"Draft {j['company']}: {e}")
                    progress.done += 1
                summary["drafted"] = drafted
            if budget.hit:
                summary["capped"] = budget.label
                progress.errors.append(f"Stopped at your spend cap ({budget.label}). Unscored roles wait for the "
                                       "next run; raise the cap under Settings → Spend to continue.")
        progress.summary = summary
        store.finish_run(run_id, summary)
    finally:
        progress.running, progress.stage, progress.message = False, "", ""
        _run_lock.release()


def run_in_background(store: Store, **kw) -> bool:
    if progress.running:
        return False
    threading.Thread(target=run, args=(store,), kwargs=kw, daemon=True).start()
    return True
