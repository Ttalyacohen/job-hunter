"""End-to-end: scrape -> stage 1 -> Gemini -> CV -> Telegram -> dedupe on the next run."""
import argparse
import asyncio
import json

import httpx
import respx

import main as app
from matcher.gemini import API

GH = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
TG = "https://api.telegram.org/botTOKEN"

BOARD = {"jobs": [
    {"id": 1, "title": "Junior ML Engineer", "absolute_url": "https://acme/1", "location": {"name": "Haifa, Israel"},
     "content": "PyTorch, CV. 0-2 years.", "updated_at": "2026-11-21T08:00:00Z"},
    {"id": 2, "title": "Senior Software Engineer", "absolute_url": "https://acme/2",
     "location": {"name": "Haifa, Israel"}, "content": "", "updated_at": "2026-11-21T08:00:00Z"},
    {"id": 3, "title": "Software Engineer", "absolute_url": "https://acme/3", "location": {"name": "Tel Aviv, Israel"},
     "content": "", "updated_at": "2026-11-21T08:00:00Z"},
    {"id": 4, "title": "Backend Developer", "absolute_url": "https://acme/4", "location": {"name": "Yokneam, Israel"},
     "content": "Java, 5+ years required", "updated_at": "2026-11-20T08:00:00Z"},
]}


def gemini_reply(request):
    body = json.loads(request.content)
    text = body["contents"][0]["parts"][0]["text"]
    evals = []
    for line in text.splitlines()[1:]:
        job = json.loads(line)
        relevant = "ML" in job["title"]
        evals.append({"job_ref": job["job_ref"], "role_type": "SOFTWARE_DEV",
                      "years_required": None if relevant else 5, "location_ok": True,
                      "match_score": 9 if relevant else 5, "track": "AI_ML_RESEARCH" if relevant else "BACKEND_FULLSTACK",
                      "is_relevant": relevant, "rejection_reason": "" if relevant else "5+ years",
                      "summary_bullets": ["PyTorch & CV", "Build models", "Thesis: PyTorch on 12 models"],
                      "tailoring_plan": {"subtitle_id": "computer_vision", "domain_line_id": "ai",
                                         "project_order": ["thesis", "radcom"], "course_ids": ["adv_cv", "cv"],
                                         "focus_skills": ["PyTorch"], "keywords": ["PyTorch"]}})
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({"evaluations": evals})}]}}]})


def run_once(monkeypatch, tmp_path, settings):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setattr(app, "load_companies", lambda: [
        {"name": "Acme", "id": "acme", "ats": "greenhouse", "board": "acme", "enabled": True}])
    settings.gemini_api_key, settings.telegram_token, settings.telegram_chat_id = "KEY", "TOKEN", "42"
    monkeypatch.setattr(app, "load_settings", lambda: settings)
    args = argparse.Namespace(dry_run=False, no_gemini=False, check_sources=False, companies=None)
    return asyncio.run(app.Pipeline(args).run())


@respx.mock
def test_full_run_and_dedupe(monkeypatch, tmp_path, settings):
    respx.get(GH).respond(json=BOARD)
    gem = respx.post(API.format(model="gemini-2.5-flash")).mock(side_effect=gemini_reply)
    msg = respx.post(f"{TG}/sendMessage").respond(json={"ok": True, "result": {"message_id": 7}})
    doc = respx.post(f"{TG}/sendDocument").respond(json={"ok": True, "result": {}})

    assert run_once(monkeypatch, tmp_path, settings) == 0
    # stage 1 dropped "Senior" and Tel Aviv on-site; Gemini saw 2 jobs in ONE batched call
    assert gem.call_count == 1
    sent = json.loads(gem.calls[0].request.content)["contents"][0]["parts"][0]["text"]
    assert "Junior ML Engineer" in sent and "Backend Developer" in sent and "Senior" not in sent
    # exactly one alert (the relevant ML job) with the tailored PDF attached as a reply
    assert msg.call_count == 1 and doc.call_count == 1
    from urllib.parse import unquote_plus
    assert "Junior ML Engineer" in unquote_plus(msg.calls[0].request.content.decode())
    assert b"CV_Talya_Cohen_Acme.pdf" in doc.calls[0].request.content
    assert b'name="reply_to_message_id"' in doc.calls[0].request.content

    seen = json.loads((tmp_path / "seen_jobs.json").read_text())["jobs"]
    assert {k: v["s"] for k, v in seen.items()} == {
        "greenhouse:acme:1": "n", "greenhouse:acme:2": "f", "greenhouse:acme:3": "f", "greenhouse:acme:4": "r"}

    # second run: nothing new -> no Gemini call, no alert
    assert run_once(monkeypatch, tmp_path, settings) == 0
    assert gem.call_count == 1 and msg.call_count == 1


@respx.mock
def test_telegram_failure_goes_to_outbox_then_retries(monkeypatch, tmp_path, settings):
    respx.get(GH).respond(json=BOARD)
    respx.post(API.format(model="gemini-2.5-flash")).mock(side_effect=gemini_reply)
    msg = respx.post(f"{TG}/sendMessage")
    msg.side_effect = [httpx.Response(400, json={"ok": False, "description": "chat not found"}),
                       httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})]
    respx.post(f"{TG}/sendDocument").respond(json={"ok": True, "result": {}})

    run_once(monkeypatch, tmp_path, settings)
    state = json.loads((tmp_path / "state.json").read_text())
    assert len(state["outbox"]) == 1

    run_once(monkeypatch, tmp_path, settings)          # outbox flushed, no re-evaluation
    state = json.loads((tmp_path / "state.json").read_text())
    seen = json.loads((tmp_path / "seen_jobs.json").read_text())["jobs"]
    assert state["outbox"] == [] and seen["greenhouse:acme:1"]["s"] == "n"


@respx.mock
def test_quota_exhausted_keeps_jobs_queued(monkeypatch, tmp_path, settings):
    respx.get(GH).respond(json=BOARD)
    quota = {"error": {"message": "quota", "details": [{"violations": [{"quotaId": "PerDay"}]}]}}
    for m in settings.gemini_models:
        respx.post(API.format(model=m)).respond(429, json=quota)
    msg = respx.post(f"{TG}/sendMessage").respond(json={"ok": True, "result": {"message_id": 1}})

    run_once(monkeypatch, tmp_path, settings)
    seen = json.loads((tmp_path / "seen_jobs.json").read_text())["jobs"]
    assert "greenhouse:acme:1" not in seen and "greenhouse:acme:4" not in seen   # still queued
    assert msg.call_count == 0
