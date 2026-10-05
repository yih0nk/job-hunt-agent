"""Every Claude call the app makes. Each one returns a validated pydantic object.

Calls: parse_resume (onboarding), score (Matcher), tailor (resume), answers (Applier).
The candidate profile goes in a cached system block so a scan that scores dozens of
roles pays for it once.
"""
from __future__ import annotations

import base64
import contextvars
import json
import urllib.error
import urllib.request
from typing import Callable, Optional, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

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

WEB_SEARCH_USD = 0.01   # $10 per 1,000 searches

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
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    cost = estimate_cost(model, u)
    if cost is not None:
        cost += searches * WEB_SEARCH_USD
    try:
        usage_hook(current_job.get(), kind, model, u, cost)
    except Exception:
        pass  # never fail a call because spend logging failed


class LLMError(RuntimeError):
    pass


def _client(settings: Settings) -> anthropic.Anthropic:
    if not settings.api_key:
        raise LLMError("Add your Anthropic API key in Settings first.")
    return anthropic.Anthropic(api_key=settings.api_key, max_retries=3)


def _call(settings: Settings, system: list[dict], content: list[dict] | str,
          out: type[T], kind: str, model: Optional[str] = None, max_tokens: int = 16000,
          tools: Optional[list[dict]] = None) -> T:
    model = model or settings.model
    if _uses_local(settings, kind) and not tools and isinstance(content, str):
        return _call_local(settings, system, content, out, kind)
    messages: list[dict] = [{"role": "user", "content": content}]
    kwargs: dict = dict(model=model, max_tokens=max_tokens, system=system, output_format=out)
    if tools:
        kwargs["tools"] = tools
    # Haiku 4.5 takes neither adaptive thinking nor effort; every newer model takes both.
    if not model.startswith("claude-haiku"):
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": settings.effort}
    if model.startswith(_FALLBACK_MODELS):
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    for _ in range(5):  # server tools (web search) can pause a long turn; resume it
        try:
            resp = _client(settings).beta.messages.parse(messages=messages, **kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError("The API key was rejected. Check it in Settings.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the Anthropic API. Try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API. Check your connection.") from e
        except ValidationError as e:  # the SDK validates the structured reply against `out`
            raise LLMError("The model's response didn't match the expected format. Try again.") from e
        _record(kind, resp.model or model, resp.usage)
        if resp.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": [
            b.model_dump(exclude_none=True, exclude={"parsed_output"}) for b in resp.content]}]
    if resp.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if resp.stop_reason in ("max_tokens", "pause_turn") or resp.parsed_output is None:
        raise LLMError("The model's response was cut off or malformed. Try again.")
    return resp.parsed_output


# --- Local models (Ollama) ------------------------------------------------------------
# Scoring and drafting can run on a local model for $0. Resume import (reads PDFs) and
# outreach (needs web search) always use Claude.

LOCAL_KINDS = {"score": "score_provider", "extract": "score_provider", "roast": "score_provider",
               "tailor": "draft_provider", "answers": "draft_provider"}


def _uses_local(settings: Settings, kind: str) -> bool:
    field = LOCAL_KINDS.get(kind)
    return bool(field and getattr(settings, field, "claude") == "local")


def _ollama(settings: Settings, path: str, body: Optional[dict] = None, timeout: int = 600) -> dict:
    url = settings.local_url.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"Local model error {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        raise LLMError(f"Can't reach Ollama at {settings.local_url}. Is it running? (ollama serve)") from e


def local_models(settings: Settings) -> list[str]:
    return sorted(m["name"] for m in _ollama(settings, "/api/tags", timeout=5).get("models", []))


def _call_local(settings: Settings, system: list[dict], content: str, out: type[T], kind: str) -> T:
    if not settings.local_model:
        raise LLMError("Pick a local model in Settings first.")
    sys_text = "\n\n".join(b["text"] for b in system if b.get("type") == "text")
    schema = out.model_json_schema()
    messages = [{"role": "system", "content": sys_text + "\n\nReply with JSON only, matching the given schema."},
                {"role": "user", "content": content}]
    last_err = ""
    for _ in range(2):  # one retry with the validation error fed back
        r = _ollama(settings, "/api/chat", {"model": settings.local_model, "messages": messages, "format": schema,
                                            "stream": False, "options": {"temperature": 0.2, "num_ctx": 32768}})
        if usage_hook:
            usage_hook(current_job.get(), kind, "local:" + settings.local_model,
                       {"input_tokens": r.get("prompt_eval_count", 0), "output_tokens": r.get("eval_count", 0),
                        "cache_write": 0, "cache_read": 0}, 0.0)
        text = (r.get("message") or {}).get("content", "")
        try:
            return out.model_validate_json(text)
        except Exception as e:
            last_err = str(e)[:500]
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"That JSON didn't match the schema: {last_err}. Reply again with valid JSON only."}]
    raise LLMError(f"The local model's reply didn't match the expected format. Try a larger model. ({last_err[:120]})")


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

TAILOR_SYSTEM = """You tailor the candidate's resume to one job. The bank in <candidate> is their
full experience; treat it as their base resume, which already fills one page. Your output is a
one-page resume a recruiter for this job reads as a clear match. Work through these steps.

1. Read the job. Note its exact nouns for the work, tools, and domain.

2. Start from the full bank. The base fills the page, so anything you add must displace
   something; prefer swapping over shrinking. Drop a bullet or entry only for something more
   relevant, or when it is clearly irrelevant here.

3. Experience (work, research, leadership): keep every entry in the bank's order (it is
   chronological). Within each entry, lead with the bullet most relevant to this job. Adopt the
   job's exact nouns where they are truthful ("post-training data pipeline", "distributed
   systems", "human-in-the-loop").

4. Projects: rank by relevance, most relevant first. Swap a weaker project out for a stronger
   bank project one-for-one rather than cramming. You may retitle a project's descriptor toward
   the role in `heading` (e.g. "Model-evaluation framework" for an AI role); use "" to keep the
   bank's. Never change the project's name.

5. Bullets:
   - NEVER fabricate. Every bullet is a rewrite of one or more bank bullets.
     source_bullet_ids[i] is the comma-separated bank bullet ids that bullets[i] is based on.
     Bullets without a real source are discarded. Never add metrics, tools, scope, ownership,
     or outcomes the sources don't state, and never upgrade a contribution ("co-trained" stays
     "co-trained", "contributed to" never becomes "built").
   - Keep every number, metric, and named tool from the source. Never shorten by cutting the
     metric.
   - Default house style (candidate.resume_rules override any of it):
     - Each bullet fits on ONE line: about 120-130 characters. The app checks this and sends
       wrapped bullets back to be shortened, so aim under.
     - Verb + what was built + 1-3 tools woven into the sentence ("in Flask and SQLite") + who
       it was for + outcome. No parenthetical tech lists.
     - Numbers a recruiter can read: before/after for intuitive units ("from 30 minutes to under
       5"), a percent otherwise. Never a range like "2-6x".
     - Bold the single headline metric of a bullet with **double asterisks**; nothing else
       (no tools, no verbs). No metric, no bold.

6. Skills: CUT before you reorder. Delete every skill this job doesn't call for, even true ones;
   a block listing everything reads as keyword-stuffing. Keep what the job names, what the
   featured entries demonstrate, and core languages, then lead each group with the job's named
   tools. Keep a credible block: the bank's groups, about three solid lines, not just the job's
   literal words. If you cut the only bullet that shows a skill, cut that skill too. Never add
   a skill the bank doesn't show.

7. headline: "" unless the bank has one and the rules don't say otherwise.

8. notes, short and honest, always including: which skills you cut; anything you deliberately
   did not claim and why (honesty guardrails); a candid fit read naming the real gaps.

candidate.resume_rules is the candidate's own house style. Follow it exactly; where it conflicts
with a default above, the rules win, except that nothing overrides the no-fabrication rule.
Write in candidate.voice."""


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


# --- Job from a pasted link -----------------------------------------------------------

class JobMeta(BaseModel):
    company: str
    title: str
    location: str


EXTRACT_SYSTEM = """You read a job posting page and return its company, job title, and location
exactly as the page states them. Use "" for anything the page does not state. The company is
the hiring company, not a job board (LinkedIn, Indeed, Greenhouse, etc.)."""


def extract_job(settings: Settings, url: str, page_title: str, text: str) -> JobMeta:
    content = f"URL: {url}\nPage title: {page_title}\n\n{text[:8000]}"
    return _call(settings, [{"type": "text", "text": EXTRACT_SYSTEM}], content, JobMeta, "extract",
                 model=settings.score_model or settings.model, max_tokens=2000)


# --- Outreach -------------------------------------------------------------------------

class Channel(BaseModel):
    kind: str          # email | linkedin | x | other
    value: str         # address or profile URL
    source: str        # where this was found (URL); emails without a public source are not allowed


class Contact(BaseModel):
    name: str
    role: str
    why: str           # one line: why this person is a good contact for this role
    source_url: str    # page that shows they work there in this capacity
    channels: list[Channel]


class OutreachPlan(BaseModel):
    contacts: list[Contact]
    email_subject: str
    email_body: str
    linkedin_note: str  # <= 300 characters (LinkedIn's connection-note limit)
    x_dm: str           # <= 280 characters
    notes: list[str]


OUTREACH_SEARCHES = 15

OUTREACH_SYSTEM = """You help a candidate follow up on a job application with a short, genuine note
to a real person at the company. You never send anything; the candidate reviews and sends.

Find people (use web search). You already have the posting, so don't search for it. Spend your
searches on people, in this order, and stop once you have 4 credible contacts:
  1. The university / early-career recruiter for this company (or this org within it):
     e.g. "<company> university recruiter", "<company> early careers recruiter <team area>".
  2. The likely hiring manager or team lead for this team: search the team's name and focus
     from the posting ("<company> <team> engineering manager", "<company> <product> lead").
  3. Engineers on that team who post publicly: LinkedIn, X, a personal site, a company
     engineering blog post, or a conference talk about this work.
Prefer people who are active publicly. Big companies have many recruiters; pick ones whose
public profile mentions this team, area, or intern hiring.
- Only professional information that is already public. For every person give source_url, the
  page showing they work there in that capacity.
- Channels: only addresses or profiles you actually found, each with the URL where you found
  it. NEVER guess or construct an email address from a name pattern. If no email is published,
  give the LinkedIn or X profile instead.
- If you can't find anyone credible, return no contacts and say so in notes. Never invent one.

Write the messages in candidate.voice, from the candidate's real experience only:
- email_subject: plain, includes the role.
- email_body: 4-6 sentences. Who they are, that they applied (or are applying) for this role,
  one specific thing from their profile that fits the team's work, and a light ask (a quick
  chat or a pointer to the right person). No flattery, no "I hope this finds you well".
  Use {name} where the recipient's first name goes.
- linkedin_note: at most 300 characters. x_dm: at most 280 characters, casual but professional.
- notes: at most 2 short sentences the candidate should know (e.g. "No public recruiter for this
  team; the team lead posts about hiring on X"). No apologies, no instructions."""


def outreach(settings: Settings, profile: Profile, prefs: Preferences, job: dict, applied: bool) -> OutreachPlan:
    system = [{"type": "text", "text": OUTREACH_SYSTEM}, _profile_block(profile, prefs)]
    # Big companies need a few searches per person; 15 leaves room for 4 contacts (~$0.15 max).
    tool = ({"type": "web_search_20250305", "name": "web_search", "max_uses": OUTREACH_SEARCHES} if settings.model.startswith("claude-haiku")
            else {"type": "web_search_20260209", "name": "web_search", "max_uses": OUTREACH_SEARCHES})
    status = "The candidate has already applied." if applied else "The candidate is about to apply."
    return _call(settings, system, f"{_job_text(job)}\n\n{status}", OutreachPlan, "outreach", tools=[tool])


# --- One-line bullets -----------------------------------------------------------------

class ShortBullet(BaseModel):
    key: str
    text: str


class ShortBullets(BaseModel):
    bullets: list[ShortBullet]


SHORTEN_SYSTEM = """These resume bullets wrap onto a second line. Rewrite each to fit on one line:
at most the given number of characters (not counting ** markers).
- Keep the headline metric (and its **bold**), the main tool, and the outcome.
- Cut filler, secondary tools, and qualifiers first. Tighten verbs ("in order to" -> "to").
- Never add anything that isn't already in the bullet. Never change what it claims.
- If candidate.resume_rules explicitly allow this particular bullet to run two lines, return it
  unchanged.
Return every key you were given."""


def shorten(settings: Settings, profile: Profile, prefs: Preferences, items: list[dict]) -> dict[str, str]:
    """items: [{key, text, max_chars}] -> {key: new text}."""
    system = [{"type": "text", "text": SHORTEN_SYSTEM}, _profile_block(profile, prefs)]
    out = _call(settings, system, json.dumps(items, indent=1), ShortBullets, "tailor", max_tokens=6000)
    return {b.key: b.text for b in out.bullets}
