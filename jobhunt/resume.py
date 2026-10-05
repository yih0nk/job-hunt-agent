"""Render a tailored resume to PDF with Typst (bundled via the `typst` wheel; no LaTeX)."""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import typst

from .models import Profile, TailoredResume
from .paths import resource_dir

# Work and research share one Experience section, as on most resumes.
SECTIONS = [(("work", "research"), "Experience"), (("project",), "Projects"),
            (("leadership",), "Leadership"), (("other",), "Other")]


def _bare(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/")


def build_data(profile: Profile, tailored: TailoredResume | None = None) -> dict:
    """Resume data for the template. Without `tailored`, renders the full bank (base resume)."""
    by_id = {e.id: e for e in profile.experience}
    if tailored:
        picked = [(by_id[t.entry_id], t.bullets, t.heading) for t in tailored.entries if t.entry_id in by_id]
        skills = tailored.skills
        headline = tailored.headline
    else:
        picked = [(e, [b.text for b in e.bullets], "") for e in profile.experience]
        skills = profile.skills
        headline = profile.headline
    sections = []
    for kinds, title in SECTIONS:
        entries = [{"kind": e.kind, "title": e.title, "org": (heading or e.org) if e.kind == "project" else e.org,
                    "location": e.location, "start": e.start, "end": e.end, "url": _bare(e.url), "tech": e.tech,
                    "bullets": bullets, "keys": [f"{e.id}:{i}" for i in range(len(bullets))]}
                   for e, bullets, heading in picked if e.kind in kinds]
        sections.append({"title": title, "entries": entries})
    links = [_bare(l.url) for l in profile.links if l.url]
    return {
        "name": profile.name or "Your Name",
        "headline": headline,
        "contact": [profile.phone, profile.email, *links],
        "education": [e.model_dump() for e in profile.education],
        "sections": sections,
        "skills": [g.model_dump() for g in skills],
    }


def wrapped_bullets(data: dict) -> dict[str, int]:
    """{"<entry_id>:<bullet index>": lines} for every bullet that takes more than one line."""
    tpl = resource_dir() / "templates" / "resume.typ"
    rows = json.loads(typst.query(str(tpl), "<bullet>", field="value", sys_inputs={"data": json.dumps(data)}))
    return {r["key"]: int(r["lines"]) for r in rows if r.get("key") and r["lines"] > 1}


def _pages(pdf: bytes) -> int:
    return max(1, len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", pdf)))


@functools.lru_cache(maxsize=64)
def _best_scale(payload: str) -> float:
    """Largest vertical-spacing multiplier (1.0-2.4) that keeps the resume on one page, so a
    page with room spreads its whitespace evenly instead of leaving a gap at the bottom."""
    tpl = str(resource_dir() / "templates" / "resume.typ")
    data = json.loads(payload)
    fits = lambda k: _pages(typst.compile(tpl, sys_inputs={"data": json.dumps({**data, "scale": k})})) == 1
    if not fits(1.0):
        return 1.0
    lo, hi = 1.0, 2.4
    if fits(hi):
        return hi
    for _ in range(7):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)
    return lo


def _spread(data: dict) -> dict:
    if "scale" in data:
        return data
    return {**data, "scale": _best_scale(json.dumps(data, sort_keys=True))}


def render_pdf(data: dict, out: Path, spread: bool = True) -> Path:
    tpl = resource_dir() / "templates" / "resume.typ"
    out.parent.mkdir(parents=True, exist_ok=True)
    typst.compile(str(tpl), output=str(out), sys_inputs={"data": json.dumps(_spread(data) if spread else data)})
    return out


def render_png(data: dict, ppi: int = 110) -> list[bytes]:
    """Page images for in-app preview (works without a PDF viewer)."""
    tpl = resource_dir() / "templates" / "resume.typ"
    out = typst.compile(str(tpl), format="png", ppi=ppi, sys_inputs={"data": json.dumps(_spread(data))})
    return out if isinstance(out, list) else [out]


def page_count(pdf: Path) -> int:
    """Count page objects (/Type /Page, not /Pages) without a PDF library."""
    raw = pdf.read_bytes()
    return max(1, len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", raw))) if raw else 0
