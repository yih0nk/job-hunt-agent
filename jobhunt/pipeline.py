"""Scout -> Matcher -> Applier, as plain functions over the Store.

Runs in a background thread; progress is exposed through `Progress` for the UI to poll.
Nothing here submits anything. A package is a PDF + drafted answers for human review.
"""
from __future__ import annotations

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
from .resume import build_data, page_count, render_pdf
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
    if job.get("description"):
        return job
    url = jd.resolve(job.get("url", ""))
    posting = jd.fetch_posting(url)
    store.update_job(job["id"], resolved_url=url, description=posting.get("text", ""))
    job.update(resolved_url=url, description=posting.get("text", ""))
    return job


def score_one(store: Store, job_id_: str) -> dict:
    llm.current_job.set(job_id_)
    prefs, profile, settings = store.preferences(), store.profile(), store.settings()
    job = _ensure_description(store, store.job(job_id_))
    fit = llm.score(settings, profile, prefs, job)
    total = llm.weighted_total(fit, prefs)
    if fit.ineligible:
        status = "ineligible"
    elif total >= prefs.thresholds.review:
        status = "review"
    else:
        status = "scored"
    store.update_job(job["id"], score=total, score_detail=fit.model_dump(), status=status)
    return store.job(job["id"])


def score_new(store: Store, limit: int = 60) -> dict:
    pending = store.jobs(["new"], limit=limit)
    progress.stage, progress.total, progress.done = "score", len(pending), 0
    counts = {"scored": 0, "review": 0, "ineligible": 0, "failed": 0}

    def work(j):
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

    with ThreadPoolExecutor(max_workers=4) as pool:
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
    posting = jd.fetch_posting(url) if url else {"questions": []}

    tailored = llm.tailor(settings, profile, prefs, job)
    out_dir = packages_dir() / f"{_slug(job['company'])}-{_slug(job['title'])}-{job['id'][:6]}"
    name = _slug(profile.name or "resume").replace("-", "_") or "resume"
    pdf = render_pdf(build_data(profile, tailored), out_dir / f"{name}_resume.pdf")
    pages = page_count(pdf)

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


def rerender(store: Store, job_id_: str, tailored: dict) -> dict:
    """User edited the tailored resume in the UI: re-render the PDF from their edits."""
    job = store.job(job_id_)
    pkg = job["package"]
    t = TailoredResume.model_validate(tailored)
    pdf = render_pdf(build_data(store.profile(), t), Path(pkg["pdf"]))
    pkg.update(tailored=t.model_dump(), pages=page_count(pdf))
    store.update_job(job_id_, package=pkg)
    return store.job(job_id_)


# --- Full run -----------------------------------------------------------------------

def run(store: Store, do_scan: bool = True, do_score: bool = True) -> None:
    if not _run_lock.acquire(blocking=False):
        return
    try:
        progress.running, progress.errors, progress.summary = True, [], {}
        run_id = store.start_run()
        summary: dict = {}
        if do_scan:
            summary["scan"] = scan(store)
        if do_score:
            summary["score"] = score_new(store)
            prefs = store.preferences()
            if prefs.auto_draft:
                ready = [j for j in store.jobs(["review"]) if (j["score"] or 0) >= prefs.thresholds.auto_draft]
                progress.stage, progress.total, progress.done = "draft", len(ready), 0
                drafted = 0
                for j in ready:
                    progress.message = f"Drafting {j['company']} — {j['title']}"
                    try:
                        draft(store, j["id"])
                        drafted += 1
                    except Exception as e:
                        progress.errors.append(f"Draft {j['company']}: {e}")
                    progress.done += 1
                summary["drafted"] = drafted
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
