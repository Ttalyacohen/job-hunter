"""Adapters against recorded-shape API responses (no network)."""
import json

import httpx
import pytest
import respx

from core.http import HttpClient
from scraper import Scraper

SCAN = {"concurrency": 5, "company_timeout_s": 10, "workday_page_size": 2, "workday_max_pages": 5,
        "workday_page_delay_s": 0}


def http():
    return HttpClient(concurrency=5, timeout=5, jitter=(0, 0), max_retries=0)


def co(name, **kw):
    return {"name": name, "id": name.lower(), "enabled": True, **kw}


def run(coro):
    import asyncio
    return asyncio.run(coro)


@respx.mock
def test_greenhouse():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true").respond(json={"jobs": [{
        "id": 123, "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
        "location": {"name": "Haifa, Israel"}, "offices": [{"name": "Haifa"}],
        "content": "&lt;p&gt;Build &lt;b&gt;things&lt;/b&gt;&lt;/p&gt;", "updated_at": "2026-11-20T10:00:00Z"}]})

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([co("Acme", ats="greenhouse", board="acme")])
    res = run(go())
    j = res.jobs[0]
    assert (j.title, j.location, j.description) == ("Software Engineer", "Haifa, Israel | Haifa", "Build \nthings")
    assert j.key == "greenhouse:acme:123"


@respx.mock
def test_lever_eu():
    respx.get("https://api.eu.lever.co/v0/postings/mobileye?mode=json").respond(json=[{
        "id": "abc", "text": "Algorithm Developer", "hostedUrl": "https://jobs.eu.lever.co/mobileye/abc",
        "categories": {"location": "Haifa, Israel", "team": "R&D"}, "workplaceType": "hybrid",
        "descriptionPlain": "Python, C++", "lists": [{"text": "Requirements", "content": "<li>B.Sc.</li>"}],
        "createdAt": 1763600000000}])

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([co("Mobileye", ats="lever", site="mobileye", region="eu")])
    j = run(go()).jobs[0]
    assert j.workplace == "hybrid" and "Requirements" in j.description and j.posted_at.startswith("2025")


@respx.mock
def test_comeet_api_and_hosted_page():
    respx.get("https://www.comeet.co/careers-api/2.0/company/AB.123/positions?token=TKN&details=true").respond(json=[{
        "uid": "P1", "name": "Backend Developer", "location": {"name": "Yokneam", "country": "IL"},
        "url_active_page": "https://acme.com/careers/p1", "details": [{"name": "Desc", "value": "<p>Go</p>", "order": 1}],
        "workplace_type": "Hybrid"}])
    page = 'x\nvar COMPANY_POSITIONS_DATA = [{"uid": "P2", "name": "ML Engineer", "location": {"city": "Haifa"}, ' \
           '"url_comeet_hosted_page": "https://www.comeet.com/jobs/acme/AB.123/ml/P2"}];\nvar Y=1;'
    respx.get("https://www.comeet.com/jobs/acme/AB.123").respond(text=page)

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([co("A", ats="comeet", uid="AB.123", token="TKN"),
                                                co("B", ats="comeet", uid="AB.123", slug="acme")])
    jobs = {j.title: j for j in run(go()).jobs}
    assert jobs["Backend Developer"].workplace == "hybrid"
    assert jobs["ML Engineer"].location == "Haifa"


@respx.mock
def test_workday_pagination_and_enrich():
    base = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External"
    pages = [
        {"total": 3, "jobPostings": [
            {"title": "Software Engineer", "externalPath": "/job/Haifa/SE_JR1", "locationsText": "Israel, Haifa",
             "postedOn": "Posted Today", "bulletFields": ["JR1"]},
            {"title": "ML Engineer", "externalPath": "/job/X/ML_JR2", "locationsText": "2 Locations",
             "postedOn": "Posted 3 Days Ago", "bulletFields": ["JR2"]}]},
        {"total": 3, "jobPostings": [
            {"title": "QA Engineer", "externalPath": "/job/Haifa/QA_JR3", "locationsText": "Israel, Haifa",
             "postedOn": "Posted 30+ Days Ago", "bulletFields": ["JR3"]}]},
    ]
    route = respx.post(f"{base}/jobs")
    probe = {"total": 3, "jobPostings": [], "facets": []}          # no location facet -> search text
    route.side_effect = [httpx.Response(200, json=p) for p in [probe, *pages]]
    respx.get(f"{base}/job/X/ML_JR2").respond(json={"jobPostingInfo": {
        "location": "Israel, Yokneam", "additionalLocations": ["Israel, Tel Aviv"], "jobDescription": "<p>PyTorch</p>",
        "externalUrl": "https://acme.wd1.myworkdayjobs.com/External/job/X/ML_JR2"}})
    company = co("Acme", ats="workday", host="acme.wd1.myworkdayjobs.com", tenant="acme", site="External",
                 search_texts=["Israel"])

    async def go():
        async with http() as h:
            s = Scraper(h, SCAN)
            res = await s.scan([company])
            ml = next(j for j in res.jobs if j.title == "ML Engineer")
            assert ml.extra["multi_location"]
            await s.enrich(ml, company)
            return res, ml
    res, ml = run(go())
    assert len(res.jobs) == 3 and json.loads(route.calls[2].request.content)["offset"] == 2
    assert json.loads(route.calls[1].request.content)["searchText"] == "Israel"
    assert ml.location == "Israel, Yokneam | Israel, Tel Aviv" and ml.description == "PyTorch"


@respx.mock
def test_workable_smartrecruiters_amazon():
    respx.get("https://apply.workable.com/api/v1/widget/accounts/acme?details=true").respond(json={"jobs": [{
        "title": "Python Developer", "shortcode": "W1", "url": "https://apply.workable.com/acme/j/W1",
        "telecommuting": True, "country": "Israel", "city": "Haifa", "description": "<p>APIs</p>"}]})
    respx.get("https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0&country=il").respond(json={
        "totalFound": 1, "content": [{"id": "S1", "name": "Junior Developer",
                                      "location": {"city": "Karmiel", "country": "il"}, "releasedDate": "2026-11-21"}]})
    respx.get(url__startswith="https://www.amazon.jobs/en/search.json").respond(json={"hits": 1, "jobs": [{
        "id_icims": "999", "title": "Software Development Engineer", "normalized_location": "Haifa, ISR",
        "job_path": "/en/jobs/999/sde", "description": "Build Nitro", "posted_date": "November 20, 2026"}]})

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([
                co("W", ats="workable", account="acme"), co("S", ats="smartrecruiters", company_id="acme"),
                co("Amazon", ats="amazon", loc_queries=["Haifa, Israel"])])
    jobs = {j.source: j for j in run(go()).jobs}
    assert jobs["workable"].workplace == "remote" and jobs["workable"].location == "Haifa, Israel"
    assert jobs["smartrecruiters"].url == "https://jobs.smartrecruiters.com/acme/S1"
    assert jobs["amazon"].url == "https://www.amazon.jobs/en/jobs/999/sde"


@respx.mock
def test_generic_jsonld_and_links():
    html = """<html><body>
      <script type="application/ld+json">{"@type":"JobPosting","title":"Algorithm Engineer",
        "url":"https://acme.com/jobs/1","jobLocation":{"address":{"addressLocality":"Haifa","addressCountry":"IL"}}}
      </script>
      <a href="/jobs/2">Junior Software Engineer</a>
      <a href="/about">About us</a>
    </body></html>"""
    respx.get("https://acme.com/careers").respond(text=html)

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([co("Acme", ats="unknown", careers_url="https://acme.com/careers",
                                                   default_location="Haifa, Israel")])
    titles = {j.title: j for j in run(go()).jobs}
    assert titles["Algorithm Engineer"].location == "Haifa, IL"
    assert "Junior Software Engineer" in titles and "About us" not in titles


@respx.mock
def test_broken_board_does_not_stop_scan():
    respx.get("https://boards-api.greenhouse.io/v1/boards/bad/jobs?content=true").respond(404)
    respx.get("https://boards-api.greenhouse.io/v1/boards/good/jobs?content=true").respond(json={"jobs": []})

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([co("Bad", ats="greenhouse", board="bad"),
                                                co("Good", ats="greenhouse", board="good")])
    res = run(go())
    assert res.failed_companies == {"bad"} and res.stats["good"]["error"] == ""


@respx.mock
def test_workday_uses_location_facet_instead_of_search():
    base = "https://nv.wd5.myworkdayjobs.com/wday/cxs/nv/Ext"
    facets = [{"facetParameter": "locationHierarchy1", "values": [
                  {"descriptor": "United States", "id": "us", "count": 900},
                  {"descriptor": "Israel", "id": "il", "count": 40}]},
              {"facetParameter": "locationMainGroup", "values": [
                  {"facetParameter": "locations", "values": [
                      {"descriptor": "Israel, Yokneam", "id": "yok", "count": 25},
                      {"descriptor": "Israel, Tel Aviv", "id": "tlv", "count": 15}]}]}]
    route = respx.post(f"{base}/jobs")
    route.side_effect = [
        httpx.Response(200, json={"total": 940, "jobPostings": [], "facets": facets}),
        httpx.Response(200, json={"total": 1, "jobPostings": [
            {"title": "Software Engineer", "externalPath": "/job/Yokneam/SE_1", "locationsText": "Israel, Yokneam",
             "bulletFields": ["JR9"]}]}),
    ]

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([co("NV", ats="workday", host="nv.wd5.myworkdayjobs.com",
                                                   tenant="nv", site="Ext")])
    res = run(go())
    body = json.loads(route.calls[1].request.content)
    # both params match "Israel" with equal coverage (40); either is a correct Israel-only filter
    assert body["appliedFacets"] in ({"locationHierarchy1": ["il"]}, {"locations": ["yok", "tlv"]})
    assert body["searchText"] == "" and len(res.jobs) == 1 and res.jobs[0].job_id == "JR9"


@respx.mock
def test_eightfold_pcsx_then_v2_fallback():
    respx.get(url__startswith="https://careers.qc.com/api/pcsx/search").respond(json={"data": {"count": 2, "positions": [
        {"id": 11, "name": "Embedded Software Engineer", "locations": ["Haifa, Israel"],
         "positionUrl": "/careers/job/11", "postedTs": 1763600000}]}})
    respx.get(url__startswith="https://ms.example/api/pcsx/search").respond(404)
    respx.get(url__startswith="https://ms.example/api/apply/v2/jobs").respond(json={"count": 1, "positions": [
        {"id": 22, "name": "Software Engineer", "location": "Haifa, Haifa District, Israel",
         "canonicalPositionUrl": "https://ms.example/careers/job/22", "t_create": 1763600000}]})

    async def go():
        async with http() as h:
            return await Scraper(h, SCAN).scan([
                co("QC", ats="eightfold", host="careers.qc.com", domain="qc.com", locations=["Haifa, Israel"]),
                co("MS", ats="eightfold", host="ms.example", domain="ms.com", locations=["Haifa, Israel"])])
    jobs = {j.company: j for j in run(go()).jobs}
    assert jobs["QC"].url == "https://careers.qc.com/careers/job/11" and jobs["QC"].location == "Haifa, Israel"
    assert jobs["MS"].title == "Software Engineer" and jobs["MS"].posted_at.startswith("2025")
