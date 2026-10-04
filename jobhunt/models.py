"""Typed documents the app stores and the UI edits.

Profile      who the candidate is + their experience bank (the only evidence tailoring
             may draw on).
Preferences  what to look for, where to look, and what is a hard no.
Settings     API key, model, effort.
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


def _id() -> str:
    return uuid.uuid4().hex[:8]


# --- Profile ------------------------------------------------------------------------

class Link(BaseModel):
    label: str = ""
    url: str = ""


class Bullet(BaseModel):
    id: str = Field(default_factory=_id)
    text: str = ""


class Entry(BaseModel):
    """One job, project, or activity. Bullets are the atomic evidence units."""
    id: str = Field(default_factory=_id)
    kind: Literal["work", "project", "leadership", "research", "other"] = "work"
    title: str = ""
    org: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    url: str = ""
    tech: list[str] = []
    bullets: list[Bullet] = []


class Education(BaseModel):
    id: str = Field(default_factory=_id)
    school: str = ""
    degree: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    gpa: str = ""
    details: list[str] = []


class SkillGroup(BaseModel):
    name: str = ""
    items: list[str] = []


class LearnedAnswer(BaseModel):
    question: str
    answer: str


class Profile(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[Link] = []
    headline: str = ""
    work_authorization: str = ""          # free text, e.g. "US citizen" or "F-1, CPT eligible"
    needs_sponsorship: Literal["no", "yes", "depends"] = "no"
    sponsorship_note: str = ""            # how to answer the sponsorship question when it's nuanced
    education: list[Education] = []
    skills: list[SkillGroup] = []
    experience: list[Entry] = []
    voice: str = "Concise and specific. No em dashes. No buzzwords. Real experience only."
    learned_answers: list[LearnedAnswer] = []

    def bullet_index(self) -> dict[str, tuple[Entry, Bullet]]:
        return {b.id: (e, b) for e in self.experience for b in e.bullets}


# --- Preferences --------------------------------------------------------------------

SourceKind = Literal["listing_repo", "github_issues", "greenhouse", "lever", "ashby", "early_career_radar"]


class Source(BaseModel):
    id: str = Field(default_factory=_id)
    kind: SourceKind
    name: str = ""
    enabled: bool = True
    # listing_repo / github_issues
    repo: str = ""
    branch: str = "main"
    file: str = "README.md"
    title_prefix: str = "New Internship"
    term: str = ""
    # greenhouse / lever / ashby: the company's board token (e.g. "stripe")
    board: str = ""
    # early_career_radar
    url: str = ""
    accept_tracks: list[str] = []


class Weights(BaseModel):
    role_fit: int = 30
    skills: int = 25
    eligibility: int = 20
    level: int = 15
    preferences: int = 10


class Thresholds(BaseModel):
    auto_draft: int = 70
    review: int = 55


class Preferences(BaseModel):
    level: Literal["internship", "new_grad", "entry", "mid", "senior", "any"] = "internship"
    target_term: str = ""                 # e.g. "Summer 2027"; empty = any
    role_keywords: list[str] = ["software", "swe", "developer", "backend", "frontend", "full stack",
                                "machine learning", "ml engineer", "ai engineer", "data engineer",
                                "infrastructure", "platform"]
    exclude_title_keywords: list[str] = ["phd", "senior", "staff", "principal", "manager", "sales"]
    exclude_companies: list[str] = []
    locations: list[str] = []             # empty = anywhere in the accepted countries
    countries: list[str] = ["United States"]
    remote_ok: bool = True
    max_age_days: Optional[float] = 14
    dealbreakers: str = ""                # free text; the matcher treats these as hard gates
    priorities: str = ""                  # free text; what makes a role more attractive
    sources: list[Source] = []
    weights: Weights = Weights()
    thresholds: Thresholds = Thresholds()
    auto_draft: bool = False              # draft packages automatically after scoring


# --- Settings -----------------------------------------------------------------------

class Settings(BaseModel):
    api_key: str = ""                     # kept in the OS keychain when one is available
    model: str = "claude-opus-5"          # drafting + resume import: where quality matters most
    score_model: str = "claude-sonnet-5"  # scoring runs on every new role, so it defaults cheaper
    effort: Literal["low", "medium", "high"] = "medium"


# --- LLM outputs (structured) -------------------------------------------------------

class SubScore(BaseModel):
    score: int
    reason: str


class FitScore(BaseModel):
    ineligible: bool
    gate: str                  # which hard gate fired, "" if none
    role_fit: SubScore
    skills: SubScore
    eligibility: SubScore
    level: SubScore
    preferences: SubScore
    summary: str               # one line: why this score
    missing: list[str]         # things the JD wants that the candidate lacks


class TailoredEntry(BaseModel):
    entry_id: str
    bullets: list[str]         # rewritten bullet text
    source_bullet_ids: list[str]  # the bank bullets each rewrite is based on (same order)


class TailoredResume(BaseModel):
    headline: str
    entries: list[TailoredEntry]
    skills: list[SkillGroup]
    notes: list[str]           # honesty gaps / what was left out and why


class DraftAnswer(BaseModel):
    question: str
    answer: str
    source: Literal["learned", "drafted"]


class AnswerSet(BaseModel):
    answers: list[DraftAnswer]
    gaps: list[str]            # questions the candidate must answer personally
