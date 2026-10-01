"""Offline tests: filters, sources parsing, rendering, and the Claude call path with the
HTTP transport mocked (so request building and response parsing are exercised for real,
without network or cost)."""
import json

import anthropic
import httpx2
import pytest

from jobhunt import filters, llm, pipeline
from jobhunt.models import (Bullet, Entry, FitScore, Preferences, Profile, Settings,
                            SkillGroup, SubScore)
from jobhunt.resume import build_data, page_count, render_pdf
from jobhunt.sources import age_to_days, parse_html_table, parse_markdown_table
from jobhunt.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUNT_HOME", str(tmp_path))
    return Store(tmp_path / "t.db")


@pytest.fixture
def profile():
    return Profile(
        name="Alex Rivera", email="a@x.com", work_authorization="US citizen",
        skills=[SkillGroup(name="Languages", items=["Go", "Python"])],
        experience=[Entry(id="e1", kind="work", title="SWE Intern", org="Acme",
                          bullets=[Bullet(id="b1", text="Cut latency 40% with a cache"),
                                   Bullet(id="b2", text="Built a Go billing service")])])


# --- filters ------------------------------------------------------------------------

def test_location_rules():
    p = Preferences(countries=["United States", "Canada"], locations=[])
    assert filters.location_ok("San Jose California US", p)
    assert filters.location_ok("Toronto, ON", p)
    assert filters.location_ok("Multiple Locations", p)
    assert filters.location_ok("", p)
    assert not filters.location_ok("Bangalore, India", p)
    assert not filters.location_ok("London (Remote)", p)
    assert filters.location_ok("Remote", p)
    assert not filters.location_ok("Remote", Preferences(remote_ok=False))


def test_location_short_tokens_case_sensitive():
    p = Preferences(locations=["SF", "LA"], countries=["United States"])
    assert filters.location_ok("SFNYC", p)
    assert not filters.location_ok("Lakewood, CO", p)   # recognizable place, not a listed one


def test_title_and_level_filters():
    p = Preferences(level="internship", role_keywords=["software", "ml"], exclude_title_keywords=["senior"])
    assert filters.passes({"company": "A", "title": "Software Engineer Intern", "location": ""}, p)[0]
    assert not filters.passes({"company": "A", "title": "Senior Software Engineer", "location": ""}, p)[0]
    assert not filters.passes({"company": "A", "title": "HTML developer", "location": ""}, p)[0]   # 'ml' is whole-word
    board_row = {"company": "A", "title": "Software Engineer", "location": "", "source_kind": "greenhouse"}
    assert filters.passes(board_row, p) == (False, "level")
    assert filters.passes({**board_row, "source_kind": "listing_repo"}, p)[0]


def test_same_position():
    assert filters.same_position(("Stripe", "Software Engineer Intern, Summer 2027"), ("Stripe", "SWE Intern"))
    assert filters.same_position(("Ramp", "Software Engineering Intern, iOS"), ("Ramp", "Software Engineer Intern - iOS"))
    assert not filters.same_position(("Ramp", "Software Engineering Intern, iOS"), ("Ramp", "Software Engineering Intern, Android"))


# --- sources ------------------------------------------------------------------------

def test_parse_markdown_table():
    md = """| Company | Role | Location | Age |
|---|---|---|---|
| **[Acme](https://acme.com)** | [SWE Intern](https://zapply.jobs/l/d/gh-acme-123) | NYC | 3d |
| ↳ | [ML Intern](https://x.y/1) | SF | 2w |"""
    rows = parse_markdown_table(md)
    assert [r["company"] for r in rows] == ["Acme", "Acme"]
    assert rows[0]["url"].startswith("https://zapply")
    assert rows[1]["age_days"] == 14


def test_parse_html_table():
    html = ('<tr><td><a href="#">Acme</a></td><td>SWE Intern</td><td>NYC</td>'
            '<td><a href="https://simplify.jobs/x">s</a><a href="https://boards.greenhouse.io/acme/jobs/1">a</a></td><td>0d</td></tr>'
            '<tr><td>↳</td><td>ML Intern</td><td>SF</td><td><a href="https://a.b">a</a></td><td>1mo</td></tr>')
    rows = parse_html_table(html)
    assert rows[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert rows[1]["company"] == "Acme" and rows[1]["age_days"] == 30


def test_age():
    assert age_to_days("12h") == 0.5 and age_to_days("3d") == 3 and age_to_days("") is None


# --- rendering ----------------------------------------------------------------------

def test_render_escapes_markup(tmp_path, profile):
    profile.experience[0].bullets[0].text = "Used *stars*, _under_, #hash, $math$ and <tags> [x]"
    out = render_pdf(build_data(profile), tmp_path / "r.pdf")
    assert out.stat().st_size > 1000 and page_count(out) == 1


# --- LLM path (mocked transport) ----------------------------------------------------

def _mock_client(payload: dict, captured: list):
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}})
    return anthropic.Anthropic(api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def _fit(score=80, ineligible=False):
    sub = {"score": score, "reason": "r"}
    return {"ineligible": ineligible, "gate": "", "role_fit": sub, "skills": sub, "eligibility": sub,
            "level": sub, "preferences": sub, "summary": "s", "missing": []}


def test_score_request_and_weighting(monkeypatch, profile):
    captured = []
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(80), captured))
    fit = llm.score(Settings(api_key="x"), profile, Preferences(), {"company": "A", "title": "T", "description": "JD"})
    assert isinstance(fit, FitScore)
    body = captured[0]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "medium"
    assert body["fallbacks"] == "default"
    assert body["system"][1]["cache_control"] == {"type": "ephemeral"}   # profile block cached
    assert llm.weighted_total(fit, Preferences()) == 80
    assert llm.weighted_total(FitScore(**_fit(90, ineligible=True)), Preferences()) == 0


def test_no_fallbacks_on_other_models(monkeypatch, profile):
    captured = []
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(), captured))
    llm.score(Settings(api_key="x", model="claude-sonnet-5"), profile, Preferences(), {"company": "A", "title": "T"})
    assert "fallbacks" not in captured[0]


def test_haiku_omits_thinking_and_effort(monkeypatch, profile):
    captured = []
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(), captured))
    llm.score(Settings(api_key="x", model="claude-haiku-4-5"), profile, Preferences(), {"company": "A", "title": "T"})
    assert "thinking" not in captured[0]
    assert "effort" not in captured[0].get("output_config", {})
    assert captured[0]["output_config"]["format"]["type"] == "json_schema"


def test_tailor_drops_untraceable_bullets(monkeypatch, profile):
    out = {"headline": "h", "skills": [{"name": "L", "items": ["Go"]}], "notes": [],
           "entries": [{"entry_id": "e1", "bullets": ["real", "invented", "merged"],
                        "source_bullet_ids": ["b1", "nope", "b1, b2"]},
                       {"entry_id": "ghost", "bullets": ["x"], "source_bullet_ids": ["b1"]}]}
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(out, []))
    t = llm.tailor(Settings(api_key="x"), profile, Preferences(), {"company": "A", "title": "T"})
    assert [e.entry_id for e in t.entries] == ["e1"]
    assert t.entries[0].bullets == ["real", "merged"]


def test_pipeline_score_and_draft(monkeypatch, store, profile):
    store.put_doc("profile", profile)
    store.put_doc("settings", Settings(api_key="x"))
    store.insert_job({"id": "j1", "company": "Acme", "title": "SWE Intern", "url": ""})
    store.update_job("j1", description="Go and SQL")
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(75), []))
    j = pipeline.score_one(store, "j1")
    assert j["status"] == "review" and j["score"] == 75

    responses = iter([
        {"headline": "h", "skills": [], "notes": ["no kafka"],
         "entries": [{"entry_id": "e1", "bullets": ["Cut latency 40%"], "source_bullet_ids": ["b1"]}]},
        {"answers": [{"question": "Why us?", "answer": "Because.", "source": "drafted"}], "gaps": []},
    ])
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(next(responses), []))
    j = pipeline.draft(store, "j1")
    assert j["status"] == "drafted"
    assert j["package"]["pages"] == 1
    assert j["package"]["answers"]["answers"][0]["question"] == "Why us?"
    # Drafted positions are deduped out of future scans.
    assert ("Acme", "SWE Intern") in store.tracked_pairs()


def test_refusal_raises(monkeypatch, profile):
    def handler(request):
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5", "content": [],
            "stop_reason": "refusal", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}})
    client = anthropic.Anthropic(api_key="t", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(llm, "_client", lambda s: client)
    with pytest.raises(llm.LLMError, match="declined"):
        llm.score(Settings(api_key="x"), profile, Preferences(), {"company": "A", "title": "T"})
