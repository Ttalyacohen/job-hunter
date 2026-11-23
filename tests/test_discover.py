"""discover_ats: detection from a careers page and by following a homepage 'Careers' link."""
import asyncio
import sys
from pathlib import Path

import respx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from core.http import HttpClient  # noqa: E402
from discover_ats import candidates_from_page  # noqa: E402


def run(url):
    async def go():
        async with HttpClient(5, 5, (0, 0), 0) as h:
            return await candidates_from_page(h, url)
    return asyncio.run(go())


@respx.mock
def test_follows_careers_link_from_homepage():
    respx.get("https://acme.io/").respond(text='<a href="/about">About</a> <a href="/company/careers">Careers</a>')
    respx.get("https://acme.io/company/careers").respond(
        text='<script src="https://www.comeet.co/careers-api/2.0/company/A1.00F/positions?token=TOKEN1234567890XYZ"></script>')
    assert run("https://acme.io/") == [{"ats": "comeet", "uid": "A1.00F", "token": "TOKEN1234567890XYZ"}]


@respx.mock
def test_detects_greenhouse_and_workday_on_page():
    respx.get("https://x.io/jobs").respond(text='<a href="https://job-boards.greenhouse.io/xco/jobs/1">a</a>'
                                                 '<a href="https://xco.wd3.myworkdayjobs.com/en-US/XCareers/job/1">b</a>')
    found = run("https://x.io/jobs")
    assert {"ats": "greenhouse", "board": "xco"} in found
    assert any(f["ats"] == "workday" and f["site"] == "XCareers" for f in found)
