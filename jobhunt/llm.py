"""Every Claude call the app makes. Each one returns a validated pydantic object.

Calls: parse_resume (onboarding), score (Matcher), tailor (resume), answers (Applier).
The candidate profile goes in a cached system block so a scan that scores dozens of
roles pays for it once.
"""
from __future__ import annotations

import base64
import contextvars
import json
from typing import Callable, Optional, TypeVar

import anthropic
from pydantic import BaseModel

from .models import (AnswerSet, Entry, FitScore, Preferences, Profile, Settings,
                     SkillGroup, TailoredResume)

T = TypeVar("T", bound=BaseModel)

# Models that support server-side refusal fallbacks.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")

# USD per million tokens: (input, output, cache read). Cache writes bill at 1.25x input.
# Estimates for the in-app spend display; the Anthropic Console is the source of truth.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}

# Set by the app: called after every API call with (job_id, kind, model, usage, cost).
usage_hook: Optional[Callable[[Optional[str], str, str, dict, Optional[float]], None]] = None
# The role a call is for, so spend can be shown per role.
current_job: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_job", default=None)


def estimate_cost(model: str, u: dict) -> Optional[float]:
    price = PRICES.get(model)
    if not price:
        return None
    pin, pout, pread = price
    return (u["input_tokens"] * pin + u["cache_write"] * pin * 1.25
            + u["cache_read"] * pread + u["output_tokens"] * pout) / 1e6


def _record(kind: str, model: str, usage) -> None:
    if usage is None or usage_hook is None:
        return
    u = {"input_tokens": usage.input_tokens or 0, "output_tokens": usage.output_tokens or 0,
         "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
         "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0}
    try:
        usage_hook(current_job.get(), kind, model, u, estimate_cost(model, u))
    except Exception:
        pass  # never fail a call because spend logging failed


class LLMError(RuntimeError):
    pass


def _client(settings: Settings) -> anthropic.Anthropic:
    if not settings.api_key:
        raise LLMError("Add your Anthropic API key in Settings first.")
    return anthropic.Anthropic(api_key=settings.api_key, max_retries=3)


def _call(settings: Settings, system: list[dict], content: list[dict] | str,
          out: type[T], kind: str, model: Optional[str] = None, max_tokens: int = 16000) -> T:
    model = model or settings.model
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=out,
    )
    # Haiku 4.5 takes neither adaptive thinking nor effort; every newer model takes both.
    if not model.startswith("claude-haiku"):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": settings.effort}
    if model.startswith(_FALLBACK_MODELS):
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    try:
        resp = _client(settings).beta.messages.parse(**kwargs)
    except anthropic.AuthenticationError as e:
        raise LLMError("The API key was rejected. Check it in Settings.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Anthropic API. Try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API. Check your connection.") from e
    _record(kind, resp.model or model, resp.usage)
    if resp.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        raise LLMError("The model's response was cut off or malformed. Try again.")
    return resp.parsed_output


def check_key(settings: Settings) -> None:
    """Cheap validity check for the Settings screen (no tokens spent)."""
    try:
        client = _client(settings)
        for m in {settings.model, settings.score_model or settings.model}:
            client.models.retrieve(m)
    except anthropic.AuthenticationError as e:
        raise LLMError("The API key was rejected.") from e
    except anthropic.NotFoundError as e:
        raise LLMError("One of the selected models is not available to this key.") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API.") from e


# --- Profile context ----------------------------------------------------------------

def _profile_block(profile: Profile, prefs: Preferences) -> dict:
    """The candidate, as the model sees them. Stable across a run -> cached."""
    p = profile.model_dump(exclude={"learned_answers"})
    body = {
        "candidate": p,
        "search": {
            "level": prefs.level,
            "target_term": prefs.target_term or "any",
            "role_keywords": prefs.role_keywords,
            "locations": prefs.locations or "anywhere in accepted countries",
            "countries": prefs.countries,
            "remote_ok": prefs.remote_ok,
            "dealbreakers": prefs.dealbreakers,
            "priorities": prefs.priorities,
        },
    }
    return {"type": "text", "text": "<candidate>\n" + json.dumps(body, indent=1) + "\n</candidate>",
            "cache_control": {"type": "ephemeral"}}


def _job_text(job: dict) -> str:
    desc = (job.get("description") or "").strip()
    return (f"<job>\ncompany: {job['company']}\ntitle: {job['title']}\n"
            f"location: {job.get('location', '')}\n\n"
            f"{desc[:24000] if desc else '(No job description could be fetched. Judge from the title and company, and say so.)'}\n</job>")


# --- Onboarding: resume -> profile --------------------------------------------------

class _PBullet(BaseModel):
    text: str


class _PEntry(BaseModel):
    kind: str
    title: str
    org: str
    location: str
    start: str
    end: str
    url: str
    tech: list[str]
    bullets: list[str]


class _PEdu(BaseModel):
    school: str
    degree: str
    location: str
    start: str
    end: str
    gpa: str
    details: list[str]


class _PLink(BaseModel):
    label: str
    url: str


class _ParsedResume(BaseModel):
    name: str
    email: str
    phone: str
    location: str
    headline: str
    links: list[_PLink]
    education: list[_PEdu]
    skills: list[SkillGroup]
    experience: list[_PEntry]


PARSE_SYSTEM = """You convert a resume into structured data for a job-search tool.
Copy facts exactly as written. Do not improve, summarize, or invent anything. Keep every
bullet verbatim as its own list item. kind is one of: work, project, leadership, research,
other. Use "" for anything the resume does not state. headline is a one-line summary built
only from what the resume says (e.g. "CS student at X focused on backend and ML")."""


def parse_resume(settings: Settings, data: bytes, filename: str) -> Profile:
    if filename.lower().endswith(".pdf"):
        content = [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.standard_b64encode(data).decode()}},
            {"type": "text", "text": "Extract this resume."},
        ]
    else:
        content = "Extract this resume:\n\n" + data.decode("utf-8", "replace")
    r = _call(settings, [{"type": "text", "text": PARSE_SYSTEM}], content, _ParsedResume, "parse")
    from .models import Bullet, Education, Link
    kinds = {"work", "project", "leadership", "research", "other"}
    return Profile(
        name=r.name, email=r.email, phone=r.phone, location=r.location, headline=r.headline,
        links=[Link(label=l.label, url=l.url) for l in r.links],
        education=[Education(**e.model_dump()) for e in r.education],
        skills=r.skills,
        experience=[Entry(kind=e.kind if e.kind in kinds else "other", title=e.title, org=e.org,
                          location=e.location, start=e.start, end=e.end, url=e.url, tech=e.tech,
                          bullets=[Bullet(text=b) for b in e.bullets])
                    for e in r.experience],
    )


# --- Matcher ------------------------------------------------------------------------

SCORE_SYSTEM = """You are the Matcher in a job-search tool. You judge how well one job fits one
candidate, honestly, so the candidate spends their time on the right applications.

Hard gates come first. Set ineligible=true and name the gate when the job clearly:
- requires citizenship, a security clearance, or a work authorization the candidate lacks
  (read the candidate's work_authorization and needs_sponsorship; a plain "we don't
  sponsor" is only a gate if the candidate actually needs sponsorship for this role),
- is at a clearly different level than the candidate is targeting (e.g. PhD-only, senior),
- is for a different term than target_term when one is set,
- is somewhere the candidate cannot work (outside their locations/countries, not remote),
- hits one of the candidate's stated dealbreakers.
Only gate on what the posting actually says. When unsure, do not gate; dock eligibility
and explain.

Then score each component 0-100:
- role_fit: how closely the role's actual work matches what the candidate is looking for.
- skills: share of the job's named requirements the candidate demonstrably has. Credit only
  skills evidenced in their experience, projects, or skills list. Never assume.
- eligibility: logistics (location, timing, authorization) are workable.
- level: the role's seniority matches the candidate's stage.
- preferences: how well it matches their stated priorities and genuine strengths.

summary is one plain sentence on why. missing lists the job's real requirements the
candidate lacks (empty if none). If no job description was available, say so in the
summary and keep scores conservative."""


def score(settings: Settings, profile: Profile, prefs: Preferences, job: dict) -> FitScore:
    system = [{"type": "text", "text": SCORE_SYSTEM}, _profile_block(profile, prefs)]
    return _call(settings, system, _job_text(job), FitScore, "score",
                 model=settings.score_model or settings.model, max_tokens=8000)


def weighted_total(fit: FitScore, prefs: Preferences) -> int:
    if fit.ineligible:
        return 0
    w = prefs.weights
    parts = [(fit.role_fit, w.role_fit), (fit.skills, w.skills), (fit.eligibility, w.eligibility),
             (fit.level, w.level), (fit.preferences, w.preferences)]
    total_w = sum(x for _, x in parts) or 1
    return round(sum(max(0, min(100, s.score)) * x for s, x in parts) / total_w)


# --- Resume tailoring ---------------------------------------------------------------

TAILOR_SYSTEM = """You tailor a one-page resume for one job from the candidate's experience bank.

Rules, all strict:
- NEVER fabricate. Every bullet you write must be a rewrite of one or more bank bullets.
  source_bullet_ids has one string per bullet, same order: source_bullet_ids[i] is the
  comma-separated bank bullet ids that bullets[i] is based on. Bullets without a real source
  are discarded. Do not add metrics, tools, scope, or outcomes the sources do not state.
- You may reorder, merge, trim, and reword bullets to use the job's vocabulary where it is
  truthful.
- Choose the entries (by entry_id) that best prove fit for this job, most relevant first.
  Aim for one page: typically 3-5 entries with 2-4 bullets each.
- skills: keep only skills the job calls for or that clearly support it, grouped. Remove
  the rest. Never add a skill the candidate's bank does not show.
- headline: one line, truthful, aimed at this role.
- notes: list honest gaps (what the job wants that the candidate lacks) and anything
  notable you left out. Keep each note short.
Write in the candidate's voice (see candidate.voice)."""


def tailor(settings: Settings, profile: Profile, prefs: Preferences, job: dict) -> TailoredResume:
    system = [{"type": "text", "text": TAILOR_SYSTEM}, _profile_block(profile, prefs)]
    out = _call(settings, system, _job_text(job), TailoredResume, "tailor")
    # Enforce the no-fabrication contract structurally: drop anything not traceable.
    bank = profile.bullet_index()
    entries = {e.id for e in profile.experience}
    clean = []
    for te in out.entries:
        if te.entry_id not in entries:
            continue
        pairs = [(b, ids) for b, ids in zip(te.bullets, _split_ids(te.source_bullet_ids, len(te.bullets)))
                 if ids and all(i in bank for i in ids)]
        if pairs:
            te.bullets = [b for b, _ in pairs]
            te.source_bullet_ids = [",".join(ids) for _, ids in pairs]
            clean.append(te)
    out.entries = clean
    return out


def _split_ids(ids: list[str], n: int) -> list[list[str]]:
    """source_bullet_ids is one string per bullet; each may hold comma-joined ids."""
    rows = [[x.strip() for x in s.split(",") if x.strip()] for s in ids]
    return rows + [[]] * (n - len(rows))


# --- Application answers ------------------------------------------------------------

ANSWER_SYSTEM = """You draft answers to job application questions for the candidate to review.

- For each question, first check candidate.learned_answers. If one clearly covers it, reuse
  it exactly and mark source=learned.
- Otherwise draft an answer (source=drafted) in the candidate's voice from their real
  experience only. Short-answer questions: 2-5 sentences, specific, no filler, no em dashes,
  no clichés like "I am passionate about". Multiple-choice: pick the literal option text.
- Work authorization and sponsorship: answer exactly per the candidate's work_authorization,
  needs_sponsorship, and sponsorship_note. If the form's wording makes the right answer
  unclear, answer with your best reading and add the question to gaps.
- Demographic / EEO questions: answer "Decline to self-identify" unless the candidate's
  learned answers say otherwise.
- gaps: questions only the candidate can answer (personal facts not in the profile,
  availability dates, salary expectations, anything you would be guessing on).
If no questions are provided, draft answers to the two most common ones for this kind of
role: "Why are you interested in this company?" and "Why are you a good fit for this role?"."""


def answers(settings: Settings, profile: Profile, prefs: Preferences, job: dict,
            questions: Optional[list[dict]]) -> AnswerSet:
    system = [{"type": "text", "text": ANSWER_SYSTEM}, _profile_block(profile, prefs)]
    learned = [a.model_dump() for a in profile.learned_answers]
    q = json.dumps(questions or [], indent=1)
    content = (f"{_job_text(job)}\n\n<learned_answers>\n{json.dumps(learned, indent=1)}\n</learned_answers>\n\n"
               f"<questions>\n{q}\n</questions>")
    return _call(settings, system, content, AnswerSet, "answers")
