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

def _mock_client(payload: dict, captured: list, usage: dict | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        captured.append(body)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": usage or {"input_tokens": 10, "output_tokens": 10}})
    return anthropic.Anthropic(api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def _fit(score=80, ineligible=False):
    sub = {"score": score, "reason": "r"}
    return {"ineligible": ineligible, "gate": "", "role_fit": sub, "skills": sub, "eligibility": sub,
            "level": sub, "preferences": sub, "summary": "s", "missing": []}


def test_score_request_and_weighting(monkeypatch, profile):
    captured = []
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(80), captured))
    fit = llm.score(Settings(api_key="x", score_model="claude-opus-5"), profile, Preferences(),
                    {"company": "A", "title": "T", "description": "JD"})
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
    llm.score(Settings(api_key="x"), profile, Preferences(), {"company": "A", "title": "T"})
    assert captured[0]["model"] == "claude-sonnet-5"      # scoring defaults to the cheaper model
    assert "fallbacks" not in captured[0]


def test_haiku_omits_thinking_and_effort(monkeypatch, profile):
    captured = []
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(), captured))
    llm.score(Settings(api_key="x", score_model="claude-haiku-4-5"), profile, Preferences(), {"company": "A", "title": "T"})
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


# --- spend tracking, keychain, one-page trim ---------------------------------------

def test_usage_logged_per_role(monkeypatch, store, profile):
    store.put_doc("profile", profile)
    store.put_settings(Settings(api_key="x"))
    store.insert_job({"id": "j1", "company": "Acme", "title": "SWE Intern"})
    store.update_job("j1", description="Go")
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    monkeypatch.setattr(llm, "usage_hook", store.log_usage)
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(_fit(70), [], usage))
    pipeline.score_one(store, "j1")
    cost = store.job("j1")["cost"]
    # Sonnet 5: 1M input at $2 + 0.1M output at $10 = $3.00
    assert cost["by_kind"] == {"score": 3.0} and cost["total"] == 3.0
    assert store.usage_summary()["all_time"] == 3.0


def test_cost_estimate_with_cache():
    u = {"input_tokens": 0, "output_tokens": 0, "cache_write": 1_000_000, "cache_read": 1_000_000}
    assert llm.estimate_cost("claude-opus-5", u) == pytest.approx(5 * 1.25 + 0.5)
    assert llm.estimate_cost("some-unknown-model", u) is None


def test_api_key_moves_to_keychain(monkeypatch, store):
    from jobhunt import keystore
    vault = {}

    class FakeKeyring:
        def get_password(self, s, u): return vault.get((s, u))
        def set_password(self, s, u, v): vault[(s, u)] = v
        def delete_password(self, s, u): vault.pop((s, u), None)

    store.put_doc("settings", Settings(api_key="sk-old"))       # saved before keychain support
    monkeypatch.setattr(keystore, "_keyring", lambda: FakeKeyring())
    keystore._cache.clear()
    assert store.settings().api_key == "sk-old"                 # still readable
    assert vault[(keystore.SERVICE, keystore.USER)] == "sk-old"  # moved to the keychain
    raw = store.get_doc("settings", Settings)
    assert raw.api_key == ""                                    # and gone from the database
    store.put_settings(Settings(api_key="sk-new"))
    assert store.settings().api_key == "sk-new" and store.get_doc("settings", Settings).api_key == ""


def test_long_resume_trimmed_to_one_page(monkeypatch, store):
    bank = [Entry(id=f"e{i}", kind="work", title=f"Role {i}", org="Org",
                  bullets=[Bullet(id=f"e{i}b{k}", text=" ".join(f"word{i}{k}{n}" for n in "abcdefgh"))
                           for k in range(8)]) for i in range(8)]
    prof = Profile(name="Long Resume", experience=bank)
    store.put_doc("profile", prof)
    store.put_settings(Settings(api_key="x"))
    store.insert_job({"id": "j2", "company": "Acme", "title": "SWE"})
    store.update_job("j2", description="Go")
    tailored = {"headline": "h", "skills": [], "notes": [],
                "entries": [{"entry_id": e.id, "bullets": [b.text for b in e.bullets],
                             "source_bullet_ids": [b.id for b in e.bullets]} for e in bank]}
    responses = iter([tailored, {"answers": [], "gaps": []}])
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(next(responses), []))
    pkg = pipeline.draft(store, "j2")["package"]
    assert pkg["pages"] == 1
    assert any("to fit one page" in n for n in pkg["tailored"]["notes"])
    entries = pkg["tailored"]["entries"]
    assert entries[0]["bullets"] and len(entries[0]["bullets"]) >= len(entries[-1]["bullets"])  # cut from the end


def test_auto_run_schedule(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBHUNT_HOME", str(tmp_path))
    from jobhunt.server import run_due
    on = Settings(api_key="x", auto_run_hours=6)
    assert run_due(on, None, 1000.0, running=False)                       # never ran
    assert not run_due(on, {"started": 0.0}, 5 * 3600, running=False)     # too soon
    assert run_due(on, {"started": 0.0}, 6 * 3600, running=False)         # due
    assert not run_due(on, {"started": 0.0}, 9 * 3600, running=True)      # already running
    assert not run_due(Settings(api_key="x"), None, 1e9, running=False)   # off by default
    assert not run_due(Settings(auto_run_hours=6), None, 1e9, running=False)  # no key


def test_background_draft_queue(monkeypatch, store, profile):
    store.put_doc("profile", profile)
    store.put_settings(Settings(api_key="x"))
    for jid in ("ok", "bad"):
        store.insert_job({"id": jid, "company": jid, "title": "SWE"})
        store.update_job(jid, description="Go", status="review")
    calls = []

    def fake_draft(st, jid):
        calls.append(jid)
        if jid == "bad":
            raise llm.LLMError("boom")
        st.update_job(jid, status="drafted")
    monkeypatch.setattr(pipeline, "draft", fake_draft)
    assert pipeline.queue_draft(store, "ok")["status"] == "drafting"
    pipeline.queue_draft(store, "bad")
    pipeline._drafts.join()
    assert calls == ["ok", "bad"]
    assert store.job("ok")["status"] == "drafted"
    bad = store.job("bad")
    assert bad["status"] == "review" and bad["last_error"] == "boom"


def test_interrupted_drafts_recover(store):
    store.insert_job({"id": "x", "company": "A", "title": "T"})
    store.update_job("x", status="drafting")
    pipeline.recover_interrupted(store)
    assert store.job("x")["status"] == "review" and "interrupted" in store.job("x")["last_error"]


def test_logo_image_checks():
    from jobhunt import logos
    png = lambda px: b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + px.to_bytes(4, "big") * 2 + b"\0" * 20
    assert logos._good(png(180), "image/png") == "png"
    assert logos._good(png(32), "image/png") is None            # too small to look sharp
    assert logos._good(png(32), "image/png", min_px=16) == "png"  # ok as a last resort
    assert logos._good(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "image/svg+xml") == "svg"
    assert logos._good(b"<html>not an image</html>", "text/html") is None
    assert logos.clean_name("🔥Waymo") == "Waymo" and logos.clean_name("Acme (YC W24)") == "Acme"
    assert logos.domain_from_url("https://jobs.ashbyhq.com/ramp/x") is None
    assert logos.domain_from_url("https://careers.acme.com/jobs/1") == "acme.com"


def test_bold_markup_renders_and_tolerates_stray_asterisks(tmp_path, profile):
    profile.experience[0].bullets[0].text = "Cut prep from **30 minutes to seconds** for **2,000+ clients**"
    profile.experience[0].bullets[1].text = "Odd ** marker with no partner and a*b"
    out = render_pdf(build_data(profile), tmp_path / "b.pdf")
    assert page_count(out) == 1


def test_resume_groups_research_under_experience(profile):
    profile.experience.append(Entry(id="r1", kind="research", title="RA", org="Lab",
                                    bullets=[Bullet(id="rb", text="Did research")]))
    sections = {s["title"]: [e["org"] for e in s["entries"]] for s in build_data(profile)["sections"]}
    assert sections["Experience"] == ["Acme", "Lab"]


def test_wrapped_bullets_are_shortened_to_one_line(monkeypatch, store, profile):
    long = "Built a Go billing reconciliation service processing 2M rows/day " * 3
    profile.experience[0].bullets[1].text = long.strip()
    store.put_doc("profile", profile)
    store.put_settings(Settings(api_key="x"))
    store.insert_job({"id": "j3", "company": "Acme", "title": "SWE"})
    store.update_job("j3", description="Go")
    tailored = {"headline": "", "skills": [], "notes": [],
                "entries": [{"entry_id": "e1", "heading": "", "bullets": ["Cut latency 40% with a cache", long.strip()],
                             "source_bullet_ids": ["b1", "b2"]}]}
    shortened = {"bullets": [{"key": "e1:1", "text": "Built a Go billing service processing **2M rows/day**"}]}
    responses = iter([tailored, shortened, {"answers": [], "gaps": []}])
    captured = []
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(next(responses), captured))
    pkg = pipeline.draft(store, "j3")["package"]
    bullets = pkg["tailored"]["entries"][0]["bullets"]
    assert "Built a Go billing service processing **2M rows/day**" in bullets
    shorten_req = json.loads(captured[1]["messages"][0]["content"])
    assert shorten_req[0]["key"] == "e1:1" and shorten_req[0]["max_chars"] <= 125
    assert not any("still run two lines" in n for n in pkg["tailored"]["notes"])


def test_short_resume_is_filled_back_to_a_full_page(monkeypatch, store, profile):
    profile.experience.append(Entry(id="p1", kind="project", title="Chess Engine", org="Bitboards",
                                    bullets=[Bullet(id="pb", text="Wrote a bitboard move generator reaching 40M nodes/sec")]))
    store.put_doc("profile", profile)
    store.put_settings(Settings(api_key="x"))
    store.insert_job({"id": "j4", "company": "Acme", "title": "SWE"})
    store.update_job("j4", description="Go")
    thin = {"headline": "", "skills": [], "notes": ["Left out Chess Engine since it is unrelated."],
            "entries": [{"entry_id": "e1", "heading": "", "bullets": ["Cut latency 40% with a cache"], "source_bullet_ids": ["b1"]}]}
    responses = iter([thin, {"answers": [], "gaps": []}])
    monkeypatch.setattr(llm, "_client", lambda s: _mock_client(next(responses), []))
    t = pipeline.draft(store, "j4")["package"]["tailored"]
    used = {i for e in t["entries"] for ids in e["source_bullet_ids"] for i in ids.split(",")}
    assert used == {"b1", "b2", "pb"}                         # every bank line came back, verbatim
    assert not any("Left out Chess Engine" in n for n in t["notes"])   # stale note removed
    assert any("Added back 2 lines" in n for n in t["notes"])


def test_duplicate_bullets_are_removed_when_fitting(tmp_path, profile):
    from jobhunt.models import TailoredEntry, TailoredResume
    t = TailoredResume(headline="", skills=[], notes=[], entries=[TailoredEntry(
        entry_id="e1", bullets=["Built a Go billing service processing **2M rows/day**", "Cut latency 40% with a cache",
                                "Built an internal billing service in Go processing 2M rows/day"],
        source_bullet_ids=["b2", "b1", "b2"])])
    pipeline.fit_page(profile, t, tmp_path / "d.pdf")
    assert t.entries[0].bullets == ["Cut latency 40% with a cache", "Built a Go billing service processing **2M rows/day**"] \
        or t.entries[0].bullets.count("Built an internal billing service in Go processing 2M rows/day") == 0


def test_greenhouse_board_token_candidates():
    from jobhunt import jd
    assert jd._token_candidates("withwaymo", "🔥Waymo")[:2] == ["withwaymo", "waymo"]
    assert "stripe" in jd._token_candidates("stripe", "Stripe")
    assert jd._greenhouse("https://careers.withwaymo.com/jobs?gh_jid=8221795") == ("withwaymo", "8221795")


def test_placeholder_descriptions_count_as_missing(monkeypatch):
    from jobhunt import jd
    monkeypatch.setattr(jd, "_fetch_posting", lambda url, hint="": {"url": url, "text": "-", "questions": [],
                                                                     "title": "", "company": "", "location": ""})
    assert jd.fetch_posting("https://x.wd5.myworkdayjobs.com/a/job/b")["text"] == ""
