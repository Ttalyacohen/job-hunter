"""Gemini client behaviour (fallback, quota) + CV grounding / one-page guarantees."""
import asyncio
import re

import httpx
import pytest
import respx
from pypdf import PdfReader

from core.models import Evaluation, Job
from core.state import RunState
from cv_generator import CVGenerator
from matcher.gemini import API, Evaluator, GeminiClient, QuotaExhausted, parse_response

JOB = Job("Acme", "acme", "greenhouse", "1", "Junior ML Engineer", "https://x", location="Haifa, Israel",
          description="PyTorch, computer vision, 0-2 years")


def gemini_ok(evals):
    import json
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({"evaluations": evals})}]}}]})


def good_eval(**over):
    e = {"job_ref": "J1", "role_type": "SOFTWARE_DEV", "years_required": None, "location_ok": True,
         "match_score": 9, "track": "AI_ML_RESEARCH", "is_relevant": True, "rejection_reason": "",
         "summary_bullets": ["PyTorch CV stack", "Train models", "Thesis in PyTorch on 12 models"],
         "tailoring_plan": {"subtitle_id": "computer_vision", "domain_line_id": "ai",
                            "project_order": ["radcom", "thesis", "BOGUS"], "course_ids": ["adv_cv", "cv", "nope"],
                            "focus_skills": ["PyTorch", "Kubernetes"], "keywords": ["PyTorch", "CNN"]}}
    e.update(over)
    return e


def evaluator(settings, profile, tmp_path):
    state = RunState(tmp_path / "state.json")
    client = GeminiClient("KEY", settings.gemini_models, state, settings.gemini)
    return Evaluator(client, profile, settings.gemini, settings.relevance), state


@respx.mock
def test_relevant_and_plan_sanitised(settings, profile, tmp_path):
    respx.post(API.format(model="gemini-2.5-flash")).mock(return_value=gemini_ok([good_eval()]))
    ev, _ = evaluator(settings, profile, tmp_path)
    res = asyncio.run(ev.evaluate([JOB]))[JOB.key]
    assert res.is_relevant and res.track == "AI_ML_RESEARCH"
    assert res.tailoring_plan["project_order"] == ["radcom", "thesis"]          # BOGUS dropped
    assert res.tailoring_plan["course_ids"] == ["adv_cv", "cv"]                 # nope dropped
    assert res.tailoring_plan["focus_skills"] == ["PyTorch"]                    # Kubernetes not hers


@pytest.mark.parametrize("over,reason", [
    ({"role_type": "QA_TEST"}, "role type"), ({"years_required": 5}, "requires 5"),
    ({"match_score": 6}, "score 6"), ({"location_ok": False}, "location"),
])
@respx.mock
def test_code_enforces_hard_rules_even_if_model_says_relevant(settings, profile, tmp_path, over, reason):
    respx.post(API.format(model="gemini-2.5-flash")).mock(return_value=gemini_ok([good_eval(**over)]))
    ev, _ = evaluator(settings, profile, tmp_path)
    res = asyncio.run(ev.evaluate([JOB]))[JOB.key]
    assert not res.is_relevant and reason in res.rejection_reason


@respx.mock
def test_daily_quota_falls_back_to_next_model(settings, profile, tmp_path):
    quota = {"error": {"code": 429, "message": "Quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
    respx.post(API.format(model="gemini-2.5-flash")).respond(429, json=quota)
    respx.post(API.format(model="gemini-3.5-flash-lite")).mock(return_value=gemini_ok([good_eval()]))
    ev, state = evaluator(settings, profile, tmp_path)
    res = asyncio.run(ev.evaluate([JOB]))[JOB.key]
    assert res.model == "gemini-3.5-flash-lite"
    assert not state.model_usable("gemini-2.5-flash")


@respx.mock
def test_retired_model_404_then_all_exhausted(settings, profile, tmp_path):
    respx.post(API.format(model="gemini-2.5-flash")).respond(404, json={"error": {"code": 404}})
    respx.post(API.format(model="gemini-3.5-flash-lite")).respond(429, json={"error": {
        "message": "limit per day", "details": [{"violations": [{"quotaId": "PerDay"}]}]}})
    ev, state = evaluator(settings, profile, tmp_path)
    with pytest.raises(QuotaExhausted):
        asyncio.run(ev.evaluate([JOB]))
    assert "gemini-2.5-flash" in state.data["gemini"]["unavailable"]


def test_parse_response_handles_fences_and_thoughts():
    payload = {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": '```json\n{"evaluations": []}\n```'}]}}]}
    assert parse_response(payload) == {"evaluations": []}


# ---- CV ---------------------------------------------------------------------
def _profile_corpus(profile) -> str:
    import yaml
    return " ".join(yaml.safe_dump(profile, allow_unicode=True, width=10_000).split())


@pytest.mark.parametrize("track", ["AI_ML_RESEARCH", "BACKEND_FULLSTACK", "SYSTEMS_LOWLEVEL_CYBER"])
def test_cv_is_one_page_and_fully_grounded(settings, profile, tmp_path, track):
    gen = CVGenerator(profile, settings.cv)
    ev = Evaluation(job_key="k", match_score=9, track=track, is_relevant=True, tailoring_plan={
        "subtitle_id": "<script>evil</script>", "project_order": ["nope"], "course_ids": ["xx"],
        "keywords": ["PyTorch", "</strong><b>"], "focus_skills": ["Rust"]})
    pdf = gen.generate(JOB, ev, tmp_path)
    assert pdf.name == "CV_Talya_Cohen_Acme.pdf"
    reader = PdfReader(pdf)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "evil" not in text and "Rust" not in text
    corpus = _profile_corpus(profile)
    # every word on the CV must come from the fact base or the fixed section headings
    headings = {"professional", "profile", "research", "projects", "engineering", "education", "technical",
                "skills", "entrepreneurship", "military", "service", "volunteering", "leadership", "selected",
                "courses:", "&"}
    for word in re.findall(r"[A-Za-z][A-Za-z+\-./']{2,}", text):
        w = word.strip(".,:;|()").lower()
        assert w in headings or w in corpus.lower(), f"ungrounded word on CV: {word}"
