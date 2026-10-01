"""Render a tailored resume to PDF with Typst (bundled via the `typst` wheel; no LaTeX)."""
from __future__ import annotations

import json
from pathlib import Path

import typst

from .models import Profile, TailoredResume
from .paths import resource_dir

SECTION_TITLES = [("work", "Experience"), ("research", "Research"), ("project", "Projects"),
                  ("leadership", "Leadership"), ("other", "Other")]


def build_data(profile: Profile, tailored: TailoredResume | None = None) -> dict:
    """Resume data for the template. Without `tailored`, renders the full bank (base resume)."""
    by_id = {e.id: e for e in profile.experience}
    if tailored:
        picked = [(by_id[t.entry_id], t.bullets) for t in tailored.entries if t.entry_id in by_id]
        skills = tailored.skills
        headline = tailored.headline
    else:
        picked = [(e, [b.text for b in e.bullets]) for e in profile.experience]
        skills = profile.skills
        headline = profile.headline
    sections = []
    for kind, title in SECTION_TITLES:
        entries = [{"title": e.title, "org": e.org, "start": e.start, "end": e.end,
                    "tech": e.tech, "bullets": bullets}
                   for e, bullets in picked if e.kind == kind]
        sections.append({"title": title, "entries": entries})
    links = [l.url.replace("https://", "").replace("http://", "").rstrip("/") for l in profile.links if l.url]
    return {
        "name": profile.name or "Your Name",
        "headline": headline,
        "contact": [profile.email, profile.phone, profile.location, *links],
        "education": [e.model_dump() for e in profile.education],
        "sections": sections,
        "skills": [g.model_dump() for g in skills],
    }


def render_pdf(data: dict, out: Path) -> Path:
    tpl = resource_dir() / "templates" / "resume.typ"
    out.parent.mkdir(parents=True, exist_ok=True)
    typst.compile(str(tpl), output=str(out), sys_inputs={"data": json.dumps(data)})
    return out


def render_png(data: dict, ppi: int = 110) -> list[bytes]:
    """Page images for in-app preview (works without a PDF viewer)."""
    tpl = resource_dir() / "templates" / "resume.typ"
    out = typst.compile(str(tpl), format="png", ppi=ppi, sys_inputs={"data": json.dumps(data)})
    return out if isinstance(out, list) else [out]


def page_count(pdf: Path) -> int:
    # Cheap page count without a PDF library: count page objects.
    raw = pdf.read_bytes()
    return max(1, raw.count(b"/Type /Page") - raw.count(b"/Type /Pages")) if raw else 0
