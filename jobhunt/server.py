"""Local HTTP API + static UI. Binds to 127.0.0.1 only.

Every /api request must carry the per-launch token that is injected into index.html, so
other websites open in the user's browser cannot drive the app (or spend their API key)
through localhost.
"""
from __future__ import annotations

import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm, logos, pipeline
from . import jd
from .jd import resolve
from .filters import job_id
from .models import LearnedAnswer, Preferences, Profile, Settings, Source
from . import keystore
from .models import TailoredResume
from .paths import data_dir, resource_dir
from .resume import build_data, render_pdf, render_png
from .sources import PRESETS
from .store import STATUSES, Store

TOKEN = os.environ.get("JOBHUNT_TOKEN") or secrets.token_urlsafe(24)
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

store = Store()
llm.usage_hook = store.log_usage


def run_due(s: Settings, last_run: Optional[dict], now: float, running: bool) -> bool:
    if s.auto_run_hours <= 0 or not s.api_key or running:
        return False
    return not last_run or now - last_run["started"] >= s.auto_run_hours * 3600


def _scheduler(stop: threading.Event) -> None:
    """Scan + score every `auto_run_hours` while the app is open."""
    while not stop.wait(60):
        try:
            if run_due(store.settings(), store.last_run(), time.time(), pipeline.progress.running):
                pipeline.run_in_background(store)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app):
    pipeline.recover_interrupted(store)
    stop = threading.Event()
    threading.Thread(target=_scheduler, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="job-hunt-agent", docs_url=None, redoc_url=None, lifespan=lifespan)


def web_dist() -> Path:
    for p in (resource_dir().parent / "web" / "dist", Path(__file__).resolve().parent.parent / "web" / "dist"):
        if p.exists():
            return p
    return Path("web/dist")


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in ALLOWED_HOSTS:          # blocks DNS-rebinding
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        # PDFs are opened in new tabs/iframes, so they may pass the token as a query param.
        tok = request.headers.get("x-jobhunt-token") or request.query_params.get("t")
        if not tok or not secrets.compare_digest(tok, TOKEN):
            return JSONResponse({"detail": "bad token"}, status_code=401)
    return await call_next(request)


def _llm_errors(fn):
    try:
        return fn()
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


# --- health / state -----------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/state")
def state():
    profile, settings = store.profile(), store.settings()
    return {
        "has_key": bool(settings.api_key),
        "has_profile": bool(profile.name and profile.experience),
        "has_sources": any(s.enabled for s in store.preferences().sources),
        "counts": store.counts(),
        "last_run": store.last_run(),
        "progress": pipeline.progress.as_dict(),
        "data_dir": str(data_dir()),
    }


# --- profile ------------------------------------------------------------------------

@app.get("/api/profile")
def get_profile() -> Profile:
    return store.profile()


@app.put("/api/profile")
def put_profile(p: Profile) -> Profile:
    store.put_doc("profile", p)
    return p


@app.post("/api/profile/import")
async def import_resume(file: UploadFile = File(...)) -> Profile:
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "File too large (15 MB max).")
    name = file.filename or "resume.pdf"
    if not name.lower().endswith((".pdf", ".txt", ".md", ".tex")):
        raise HTTPException(400, "Upload a PDF, or paste your resume as .txt/.md.")
    parsed = _llm_errors(lambda: llm.parse_resume(store.settings(), data, name))
    # Keep what the user already set that a resume can't tell us.
    cur = store.profile()
    parsed.work_authorization = cur.work_authorization
    parsed.needs_sponsorship = cur.needs_sponsorship
    parsed.sponsorship_note = cur.sponsorship_note
    parsed.voice = cur.voice
    parsed.learned_answers = cur.learned_answers
    return parsed  # not saved: the UI shows it for review, then PUTs it


@app.get("/api/profile/resume.pdf")
def base_resume():
    out = data_dir() / "base_resume.pdf"
    render_pdf(build_data(store.profile()), out)
    return FileResponse(out, media_type="application/pdf")


# --- preferences / settings ---------------------------------------------------------

@app.get("/api/preferences")
def get_prefs() -> Preferences:
    return store.preferences()


@app.put("/api/preferences")
def put_prefs(p: Preferences) -> Preferences:
    store.put_doc("preferences", p)
    return p


@app.get("/api/presets")
def presets():
    return PRESETS


class SettingsView(BaseModel):
    has_key: bool
    key_hint: str
    key_in_keychain: bool
    model: str
    score_model: str
    effort: str
    auto_run_hours: int
    logos: bool


def _settings_view(s: Settings) -> SettingsView:
    return SettingsView(has_key=bool(s.api_key), key_hint=("…" + s.api_key[-4:]) if s.api_key else "",
                        key_in_keychain=bool(s.api_key) and keystore.get() == s.api_key,
                        model=s.model, score_model=s.score_model, effort=s.effort,
                        auto_run_hours=s.auto_run_hours, logos=s.logos)


@app.get("/api/settings")
def get_settings() -> SettingsView:
    return _settings_view(store.settings())


class SettingsIn(BaseModel):
    api_key: Optional[str] = None     # None = keep current
    model: Optional[str] = None
    score_model: Optional[str] = None
    effort: Optional[str] = None
    auto_run_hours: Optional[int] = None
    logos: Optional[bool] = None


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> SettingsView:
    s = store.settings()
    if body.api_key is not None:
        s.api_key = body.api_key.strip()
    if body.model:
        s.model = body.model
    if body.score_model:
        s.score_model = body.score_model
    if body.effort in ("low", "medium", "high"):
        s.effort = body.effort
    if body.auto_run_hours is not None:
        s.auto_run_hours = max(0, min(168, body.auto_run_hours))
    if body.logos is not None:
        s.logos = body.logos
    store.put_settings(s)
    return _settings_view(s)


@app.get("/api/usage")
def usage():
    return store.usage_summary()


@app.post("/api/settings/test")
def test_settings():
    _llm_errors(lambda: llm.check_key(store.settings()))
    return {"ok": True}


# --- pipeline -----------------------------------------------------------------------

class RunIn(BaseModel):
    scan: bool = True
    score: bool = True


@app.post("/api/run")
def run(body: RunIn):
    if body.score and not store.settings().api_key:
        raise HTTPException(400, "Add your Anthropic API key in Settings first.")
    started = pipeline.run_in_background(store, do_scan=body.scan, do_score=body.score)
    return {"started": started, "progress": pipeline.progress.as_dict()}


@app.get("/api/progress")
def get_progress():
    return pipeline.progress.as_dict()


# --- jobs ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs(status: str = ""):
    statuses = [s for s in status.split(",") if s] or None
    rows = store.jobs(statuses)
    for r in rows:
        r.pop("description", None)   # keep the list payload small
    return rows


class NewJob(BaseModel):
    company: str
    title: str
    url: str = ""
    location: str = ""
    description: str = ""


@app.post("/api/jobs")
def add_job(j: NewJob):
    """Manually add a role (e.g. a link a friend sent)."""
    jid = job_id(j.company, j.title, j.location)
    store.insert_job({"id": jid, "company": j.company, "title": j.title, "url": j.url,
                      "location": j.location, "source": "manual"})
    if j.description:
        store.update_job(jid, description=j.description, resolved_url=resolve(j.url))
    return store.job(jid)


class FromUrl(BaseModel):
    url: str


@app.post("/api/jobs/from-url")
def add_job_from_url(body: FromUrl):
    """Paste any job link: resolve it, fetch the posting, fill in company/title/location."""
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    resolved = resolve(url)
    posting = jd.fetch_posting(resolved)
    if not posting["text"] and posting.get("error"):
        raise HTTPException(400, "Couldn't open that link. Check the URL, or add the role by hand.")
    company, title, location = posting["company"], posting["title"], posting["location"]
    if not (company and title):
        meta = _llm_errors(lambda: llm.extract_job(store.settings(), resolved, posting.get("page_title", ""), posting["text"]))
        company, title, location = company or meta.company, title or meta.title, location or meta.location
    if not (company and title):
        raise HTTPException(400, "Couldn't tell which company and role that link is for. Add it by hand instead.")
    jid = job_id(company, title, location)
    store.insert_job({"id": jid, "company": company, "title": title, "url": url, "location": location, "source": "link"})
    store.update_job(jid, description=posting["text"], resolved_url=resolved)
    return store.job(jid)


@app.post("/api/jobs/refetch-descriptions")
def refetch_descriptions():
    """Free backfill of empty job descriptions; lists scored roles worth re-scoring."""
    return pipeline.backfill_descriptions(store)


@app.post("/api/jobs/rescore")
def rescore(body: dict = Body(...)):
    """Re-score the given roles in the background (e.g. ones first scored without a description)."""
    ids = [i for i in body.get("ids", []) if store.job(i)]
    if not store.settings().api_key:
        raise HTTPException(400, "Add your Anthropic API key in Settings first.")
    def work():
        for i in ids:
            try:
                pipeline.score_one(store, i)
            except Exception:
                pass
    threading.Thread(target=work, daemon=True).start()
    return {"started": len(ids)}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = store.job(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j


class JobPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@app.patch("/api/jobs/{jid}")
def patch_job(jid: str, body: JobPatch):
    if not store.job(jid):
        raise HTTPException(404, "No such job")
    fields = {}
    if body.status:
        if body.status not in STATUSES:
            raise HTTPException(400, "Unknown status")
        fields["status"] = body.status
        if body.status == "applied":
            import time
            fields["applied_at"] = time.time()
    if body.notes is not None:
        fields["notes"] = body.notes
    store.update_job(jid, **fields)
    return store.job(jid)


@app.post("/api/jobs/{jid}/score")
def score_job(jid: str):
    if not store.job(jid):
        raise HTTPException(404, "No such job")
    return _llm_errors(lambda: pipeline.score_one(store, jid))


@app.post("/api/jobs/{jid}/draft")
def draft_job(jid: str, background: bool = False):
    if not store.job(jid):
        raise HTTPException(404, "No such job")
    if background:
        if not store.settings().api_key:
            raise HTTPException(400, "Add your Anthropic API key in Settings first.")
        return pipeline.queue_draft(store, jid)
    return _llm_errors(lambda: pipeline.draft(store, jid))


@app.post("/api/jobs/{jid}/outreach")
def outreach_job(jid: str):
    if not store.job(jid):
        raise HTTPException(404, "No such job")
    return _llm_errors(lambda: pipeline.outreach(store, jid))


@app.post("/api/jobs/{jid}/fill")
def fill_job(jid: str):
    j = store.job(jid)
    if not j or not j.get("package"):
        raise HTTPException(404, "No package for this job")
    return pipeline.refill(store, jid)


@app.put("/api/jobs/{jid}/tailored")
def edit_tailored(jid: str, tailored: dict = Body(...)):
    j = store.job(jid)
    if not j or not j.get("package"):
        raise HTTPException(404, "No package for this job")
    return pipeline.rerender(store, jid, tailored)


@app.get("/api/jobs/{jid}/resume.pdf")
def job_pdf(jid: str):
    j = store.job(jid)
    if not j or not j.get("package"):
        raise HTTPException(404, "No package for this job")
    return FileResponse(j["package"]["pdf"], media_type="application/pdf",
                        filename=Path(j["package"]["pdf"]).name, content_disposition_type="inline")


def _png(pages: list[bytes], page: int) -> Response:
    if not 0 <= page < len(pages):
        raise HTTPException(404, "No such page")
    return Response(pages[page], media_type="image/png", headers={"x-page-count": str(len(pages))})


@app.get("/api/jobs/{jid}/resume.png")
def job_png(jid: str, page: int = 0):
    j = store.job(jid)
    if not j or not j.get("package"):
        raise HTTPException(404, "No package for this job")
    t = TailoredResume.model_validate(j["package"]["tailored"])
    return _png(render_png(build_data(store.profile(), t)), page)


@app.get("/api/profile/resume.png")
def base_png(page: int = 0):
    return _png(render_png(build_data(store.profile())), page)


@app.get("/api/logo")
def logo(company: str, url: str = ""):
    """Company logo PNG, cached on disk; 404 means 'use the letter avatar'."""
    if not store.settings().logos:
        raise HTTPException(404)
    found = logos.logo(company, url)
    if not found:
        raise HTTPException(404, headers={"Cache-Control": "max-age=3600"})
    img, media_type = found
    # SVGs come from third-party sites: never let one run script if opened directly.
    return Response(img, media_type=media_type, headers={
        "Cache-Control": "max-age=604800", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff"})


@app.post("/api/learned")
def learn(a: LearnedAnswer):
    """Save an approved answer so future drafts reuse it verbatim."""
    p = store.profile()
    key = a.question.strip().lower()
    p.learned_answers = [x for x in p.learned_answers if x.question.strip().lower() != key] + [a]
    store.put_doc("profile", p)
    return p.learned_answers


# --- UI -----------------------------------------------------------------------------

def _index() -> HTMLResponse:
    idx = web_dist() / "index.html"
    if not idx.exists():
        return HTMLResponse("<p>UI not built. Run <code>npm run build</code> in web/.</p>")
    html = idx.read_text().replace("</head>", f'<meta name="jobhunt-token" content="{TOKEN}"></head>', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    return _index()


if (web_dist() / "assets").exists():
    app.mount("/assets", StaticFiles(directory=web_dist() / "assets"), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    f = web_dist() / path
    if path and f.is_file() and web_dist() in f.resolve().parents:
        return FileResponse(f)
    return _index()
