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


def test_outreach_resumes_paused_web_search(monkeypatch, profile):
    plan = {"contacts": [{"name": "Dana Lee", "role": "University Recruiter", "why": "Runs intern hiring",
                          "source_url": "https://acme.com/team", "channels": [
                              {"kind": "linkedin", "value": "https://linkedin.com/in/dana", "source": "https://acme.com/team"}]}],
            "email_subject": "SWE Intern application", "email_body": "Hi {name}, ...", "linkedin_note": "Hi Dana",
            "x_dm": "Hi", "notes": []}
    bodies, turn = [], iter(["pause_turn", "end_turn"])

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        stop = next(turn)
        content = ([{"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "Acme recruiter"}}]
                   if stop == "pause_turn" else [{"type": "text", "text": json.dumps(plan)}])
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": body["model"],
                                          "content": content, "stop_reason": stop, "stop_sequence": None,
                                          "usage": {"input_tokens": 10, "output_tokens": 10,
                                                    "server_tool_use": {"web_search_requests": 3}}})
    client = anthropic.Anthropic(api_key="t", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(llm, "_client", lambda s: client)
    costs = []
    monkeypatch.setattr(llm, "usage_hook", lambda *a: costs.append(a[4]))
    out = llm.outreach(Settings(api_key="x"), profile, Preferences(), {"company": "Acme", "title": "SWE"}, applied=True)
    assert out.contacts[0].name == "Dana Lee"
    assert bodies[0]["tools"][0]["type"] == "web_search_20260209"
    assert bodies[1]["messages"][-1]["role"] == "assistant"            # paused turn sent back to continue
    assert costs[0] > 0.03                                               # 3 searches at $0.01 counted


def test_local_model_scores_with_retry_on_bad_json(monkeypatch, profile):
    calls = []

    def fake_ollama(settings, path, body=None, timeout=600):
        calls.append(body)
        text = "not json" if len(calls) == 1 else json.dumps(_fit(66))
        return {"message": {"content": text}, "prompt_eval_count": 100, "eval_count": 20}
    monkeypatch.setattr(llm, "_ollama", fake_ollama)
    logged = []
    monkeypatch.setattr(llm, "usage_hook", lambda *a: logged.append(a))
    s = Settings(api_key="", score_provider="local", local_model="qwen3:14b")
    fit = llm.score(s, profile, Preferences(), {"company": "A", "title": "T"})
    assert fit.role_fit.score == 66
    assert len(calls) == 2 and calls[0]["format"]["type"] == "object"   # schema-constrained
    assert "didn't match the schema" in calls[1]["messages"][-1]["content"]
    assert logged[0][2] == "local:qwen3:14b" and logged[0][4] == 0.0     # free, but still counted


def test_local_needs_a_model(profile):
    with pytest.raises(llm.LLMError, match="local model"):
        llm.score(Settings(score_provider="local"), profile, Preferences(), {"company": "A", "title": "T"})


# --- sourcing: new boards, JSON-LD, auto-detect --------------------------------------

def _src(**kw):
    from jobhunt.models import Source
    return Source(**kw)


def test_workday_age():
    from jobhunt.sources import workday_age
    assert workday_age("Posted Today") == 0 and workday_age("Posted Yesterday") == 1
    assert workday_age("Posted 3 Days Ago") == 3 and workday_age("Posted 30+ Days Ago") == 31
    assert workday_age("Posted 6 Hours Ago") == 0.25 and workday_age("") is None


def test_fetch_workday_pages_and_urls(monkeypatch):
    from jobhunt import sources
    pages = {0: {"total": 25, "jobPostings": [{"title": f"SWE Intern {i}", "externalPath": f"/job/SF/SWE_{i}",
                                               "locationsText": "San Francisco, CA", "postedOn": "Posted 2 Days Ago"} for i in range(20)]},
             20: {"total": 25, "jobPostings": [{"title": f"SWE Intern {i}", "externalPath": f"/job/SF/SWE_{i}",
                                                "locationsText": "Remote", "postedOn": "Posted Today"} for i in range(20, 25)]}}
    calls = []
    monkeypatch.setattr(sources, "fetch_json_post", lambda url, body: (calls.append((url, body)), pages[body["offset"]])[1])
    rows = sources.fetch_source(_src(kind="workday", board="acme|wd5|External", name="Acme"))
    assert len(rows) == 25 and len(calls) == 2
    assert calls[0][0] == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    assert rows[0]["url"] == "https://acme.wd5.myworkdayjobs.com/External/job/SF/SWE_0"   # the shape jd.py resolves
    assert rows[0]["age_days"] == 2 and rows[-1]["age_days"] == 0 and rows[0]["source_kind"] == "workday"


def test_fetch_smartrecruiters_workable_bamboohr_recruitee(monkeypatch):
    from jobhunt import sources
    payloads = {
        "smartrecruiters.com/v1/companies/Visa/postings": {"totalFound": 1, "content": [
            {"id": "123", "name": "Software Engineer Intern", "releasedDate": "2026-10-01T00:00:00.000Z",
             "location": {"city": "Austin", "region": "TX", "country": "us", "remote": False}, "company": {"name": "Visa"}}]},
        "workable.com/api/v1/widget/accounts/acme": {"name": "Acme", "jobs": [
            {"title": "ML Intern", "url": "https://apply.workable.com/acme/j/ABC/", "published_on": "2026-10-02",
             "location": {"city": "Berlin", "country": "Germany"}, "telecommuting": True, "description": "<p>Do ML</p>"}]},
        "acme.bamboohr.com/careers/list": {"result": [
            {"id": 7, "jobOpeningName": "Data Engineer Intern", "location": {"city": "Denver", "state": "CO"}, "isRemote": False}]},
        "acme.recruitee.com/api/offers/": {"offers": [
            {"title": "Backend Intern", "careers_url": "https://acme.recruitee.com/o/backend", "city": "Amsterdam",
             "country": "Netherlands", "remote": True, "published_at": "2026-10-03T10:00:00Z", "description": "<b>Go</b>"}]},
    }
    monkeypatch.setattr(sources, "fetch_json", lambda url, timeout=30: next(v for k, v in payloads.items() if k in url))
    sr = sources.fetch_source(_src(kind="smartrecruiters", board="Visa"))
    assert sr[0]["url"] == "https://jobs.smartrecruiters.com/Visa/123" and sr[0]["location"] == "Austin, TX, us"
    wk = sources.fetch_source(_src(kind="workable", board="acme"))
    assert wk[0]["company"] == "Acme" and wk[0]["location"] == "Berlin, Germany (Remote)" and wk[0]["description"] == "Do ML"
    bh = sources.fetch_source(_src(kind="bamboohr", board="acme"))
    assert bh[0]["url"] == "https://acme.bamboohr.com/careers/7" and bh[0]["location"] == "Denver, CO"
    rc = sources.fetch_source(_src(kind="recruitee", board="acme"))
    assert rc[0]["location"] == "Amsterdam, Netherlands (Remote)" and rc[0]["description"] == "Go"


def test_careers_page_jsonld(monkeypatch):
    from jobhunt import sources
    page = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"Organization","name":"Acme"},
      {"@type":"JobPosting","title":"Software Engineer Intern","datePosted":"2026-10-04","url":"https://acme.com/jobs/1",
       "hiringOrganization":{"@type":"Organization","name":"Acme"},
       "jobLocation":{"@type":"Place","address":{"addressLocality":"Seattle","addressRegion":"WA","addressCountry":"US"}},
       "description":"<p>Build things</p>"}]}</script>
    <script type="application/ld+json">{"@type":"ItemList","itemListElement":[{"@type":"ListItem","item":
      {"@type":"JobPosting","title":"Remote SRE Intern","jobLocationType":"TELECOMMUTE","datePosted":"2026-10-05"}}]}</script></html>"""
    monkeypatch.setattr(sources, "fetch", lambda url, timeout=30: page)
    rows = sources.fetch_source(_src(kind="careers_page", url="https://acme.com/careers"))
    assert [r["title"] for r in rows] == ["Software Engineer Intern", "Remote SRE Intern"]
    assert rows[0]["company"] == "Acme" and rows[0]["location"] == "Seattle, WA, US" and rows[0]["description"] == "Build things"
    assert rows[1]["location"] == "(Remote)" and rows[0]["source"] == "acme.com"


def test_detect_source_url_shapes():
    from jobhunt.sources import detect_source as d
    assert d("https://boards.greenhouse.io/stripe/jobs/123", sniff=False) == {"kind": "greenhouse", "board": "stripe", "name": "Stripe"}
    assert d("https://job-boards.greenhouse.io/anthropic", sniff=False)["board"] == "anthropic"
    assert d("https://boards.greenhouse.io/embed/job_board?for=xai", sniff=False)["board"] == "xai"
    assert d("https://jobs.lever.co/ramp/abc", sniff=False)["kind"] == "lever"
    assert d("https://jobs.ashbyhq.com/openai", sniff=False) == {"kind": "ashby", "board": "openai", "name": "Openai"}
    assert d("https://acme.wd5.myworkdayjobs.com/en-US/External_Careers/job/x", sniff=False)["board"] == "acme|wd5|External_Careers"
    assert d("https://jobs.smartrecruiters.com/Visa/123", sniff=False)["board"] == "Visa"
    assert d("https://apply.workable.com/acme/j/ABC", sniff=False)["kind"] == "workable"
    assert d("https://acme.bamboohr.com/careers", sniff=False)["kind"] == "bamboohr"
    assert d("https://acme.recruitee.com/o/x", sniff=False)["kind"] == "recruitee"
    assert d("https://github.com/SimplifyJobs/New-Grad-Positions", sniff=False)["repo"] == "SimplifyJobs/New-Grad-Positions"
    assert d("https://acme.com/careers", sniff=False) is None and d("", sniff=False) is None


def test_detect_source_sniffs_custom_pages(monkeypatch):
    from jobhunt import sources
    pages = {"https://a.com/careers": '<iframe src="https://jobs.ashbyhq.com/a-co"></iframe>',
             "https://b.com/jobs": '<script type="application/ld+json">{"@type":"JobPosting","title":"x"}</script>',
             "https://c.com/": "<html>nothing here</html>"}
    monkeypatch.setattr(sources, "fetch", lambda url, timeout=30: pages[url])
    assert sources.detect_source("https://a.com/careers") == {"kind": "ashby", "board": "a-co", "name": "A Co"}
    assert sources.detect_source("https://b.com/jobs") == {"kind": "careers_page", "url": "https://b.com/jobs", "name": "B"}
    assert sources.detect_source("https://c.com/") is None


def test_norm_company_ignores_legal_suffixes():
    assert filters.same_position(("Stripe, Inc.", "SWE Intern"), ("Stripe", "Software Engineer Intern"))
    assert filters.same_position(("Acme Corp", "Data Engineer Intern"), ("ACME (US) LLC", "Data Engineering Intern"))


# --- tracker import / export ----------------------------------------------------------

def test_tracker_parse_csv_markdown_and_lines():
    from jobhunt import tracker
    csv_rows, mapping = tracker.parse_tracker(
        b"Employer,Position,Where,Stage,Date Applied,Link\n"
        b"Stripe,Software Engineer Intern,SF,Phone screen,2026-09-20,https://stripe.com/j/1\n"
        b"Ramp,SWE Intern,NYC,Rejected,09/25/2026,\n"
        b"Ramp,Software Engineering Intern,NYC,applied,,\n"      # same position as the row above: dropped
        b"Acme,ML Intern,,,,\n", "apps.csv")
    assert mapping == {"Employer": "company", "Position": "title", "Where": "location", "Stage": "status",
                       "Date Applied": "applied_at", "Link": "url"}
    assert [(r["company"], r["status"]) for r in csv_rows] == [("Stripe", "interviewing"), ("Ramp", "rejected"), ("Acme", "applied")]
    import datetime as _dt
    utc = lambda y, m, d: _dt.datetime(y, m, d, 12, tzinfo=_dt.timezone.utc).timestamp()   # noon UTC
    assert csv_rows[0]["applied_at"] == utc(2026, 9, 20) and csv_rows[1]["applied_at"] == utc(2026, 9, 25) and csv_rows[2]["applied_at"] is None
    assert csv_rows[0]["url"] == "https://stripe.com/j/1"
    md_rows, _ = tracker.parse_tracker(b"| Company | Role | Status |\n|---|---|---|\n"
                                       b"| [Cohere](https://cohere.com) | [Research Intern](https://cohere.com/j/9) | Offer! |\n", "t.md")
    assert md_rows[0]["url"] == "https://cohere.com/j/9" and md_rows[0]["status"] == "offer"
    line_rows, _ = tracker.parse_tracker(b"Figma - Product Eng Intern - withdrawn\nNotion | SWE Intern\n")
    assert [(r["company"], r["status"]) for r in line_rows] == [("Figma", "archived"), ("Notion", "applied")]


def test_tracker_status_vocabulary():
    from jobhunt.tracker import norm_status as n
    assert n("OA sent") == "interviewing" and n("Final round") == "interviewing" and n("Hired!") == "offer"
    assert n("Rejected after interview") == "rejected" and n("Ghosted") == "rejected"
    assert n("") == "applied" and n("Submitted ✅") == "applied" and n("Not applying") == "archived"


def test_tracker_import_feeds_scan_dedup(monkeypatch, store):
    from jobhunt import tracker
    from jobhunt.models import Source
    rows, _ = tracker.parse_tracker(b"Company,Role,Status\nStripe,Software Engineer Intern,applied\nRamp,SWE Intern,interview\n")
    assert tracker.apply(store, rows) == {"added": 2, "updated": 0, "skipped": 0}
    jobs = {j["company"]: j for j in store.jobs()}
    assert jobs["Stripe"]["status"] == "applied" and jobs["Ramp"]["status"] == "interviewing"
    assert jobs["Stripe"]["source"] == "tracker import" and jobs["Stripe"]["applied_at"] is None   # no date in the file: none invented
    # A second import of the same file changes nothing.
    assert tracker.apply(store, rows) == {"added": 0, "updated": 0, "skipped": 2}
    # The scanner now skips those positions, even with different wording, and still takes new ones.
    store.put_doc("preferences", Preferences(sources=[Source(kind="greenhouse", board="stripe")]))
    monkeypatch.setattr(pipeline, "fetch_source", lambda src: [
        {"company": "Stripe, Inc.", "title": "Software Engineering Intern, Summer 2027", "location": "", "url": "", "age_days": 1, "source_kind": "greenhouse"},
        {"company": "Stripe", "title": "Software Engineer Internship (Summer 2027)", "location": "", "url": "", "age_days": 1, "source_kind": "greenhouse"},
        {"company": "Figma", "title": "Software Engineer Intern", "location": "", "url": "", "age_days": 1, "source_kind": "greenhouse"}])
    counts = pipeline.scan(store)
    assert counts["already_tracked"] == 2 and counts["new"] == 1


def test_tracker_import_never_regresses_a_status(store):
    from jobhunt import tracker
    store.insert_job({"id": "j1", "company": "Stripe", "title": "SWE Intern"})
    store.update_job("j1", status="offer")
    back = [{"company": "Stripe", "title": "Software Engineer Intern", "location": "", "url": "", "status": "applied", "applied_at": None}]
    assert tracker.preview(store, back)["rows"][0]["action"] == "keep"
    assert tracker.apply(store, back) == {"added": 0, "updated": 0, "skipped": 1} and store.job("j1")["status"] == "offer"
    done = [{**back[0], "status": "rejected"}]
    assert tracker.preview(store, done)["rows"][0]["action"] == "update"
    tracker.apply(store, done)
    assert store.job("j1")["status"] == "rejected"


def test_tracker_export_round_trips_csv_and_markdown(store):
    from jobhunt import tracker
    rows, _ = tracker.parse_tracker(b"Company,Role,Location,Status,Applied,Link\n"
                                    b"Stripe,Software Engineer Intern,SF,interviewing,2026-09-20,https://stripe.com/j/1\n"
                                    b"Ramp,SWE Intern,NYC,rejected,,\n")
    tracker.apply(store, rows)
    store.insert_job({"id": "d1", "company": "Figma", "title": "PM Intern"})       # drafted: not an application yet
    store.update_job("d1", status="drafted")
    out = tracker.export_rows(store)
    assert [r[0] for r in out] == ["Stripe", "Ramp"] and out[0][3] == "interviewing" and out[0][4] == "2026-09-20"
    for text, name in ((tracker.to_csv(out), "a.csv"), (tracker.to_markdown(out), "a.md")):
        back, mapping = tracker.parse_tracker(text.encode(), name)
        assert mapping["Company"] == "company" and mapping["Applied"] == "applied_at" and mapping["Link"] == "url"
        assert [(r["company"], r["title"], r["status"]) for r in back] == [("Stripe", "Software Engineer Intern", "interviewing"),
                                                                            ("Ramp", "SWE Intern", "rejected")]
        assert back[0]["url"] == "https://stripe.com/j/1" and back[0]["applied_at"] == rows[0]["applied_at"]
        assert tracker.preview(store, back)["counts"] == {"same": 2}           # re-importing an export changes nothing
