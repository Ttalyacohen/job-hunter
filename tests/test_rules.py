import pytest

from core.models import Job
from matcher.rules import RuleFilter


@pytest.fixture
def rules(filters):
    return RuleFilter(filters)


def job(title="Software Engineer", location="Haifa, Israel", workplace=""):
    return Job("Acme", "acme", "test", "1", title, "https://x", location=location, workplace=workplace)


@pytest.mark.parametrize("title", [
    "Software Engineer", "Junior Backend Developer", "Full-Stack Developer", "Fullstack Engineer",
    "Algorithm Developer - Computer Vision", "ML Engineer", "Machine Learning Engineer, New Grad",
    "AI Researcher", "Embedded Software Engineer", "C++ Developer", "Software Engineer II",
    "Security Researcher", "Data Scientist", "Applied Scientist", "Deep Learning Engineer",
    "מפתח/ת תוכנה", "Software Development Engineer, Annapurna Labs",
])
def test_target_titles_pass(rules, title):
    assert rules.check_title(title).ok, title


@pytest.mark.parametrize("title", [
    "Senior Software Engineer", "Sr. Backend Developer", "Staff ML Engineer", "Principal Engineer",
    "Software Team Lead", "Engineering Manager", "QA Engineer", "Software Test Engineer",
    "Automation Test Engineer", "SDET", "Design Verification Engineer", "Validation Engineer",
    "DevOps Engineer", "IT Specialist", "SOC Analyst", "NOC Engineer", "Technical Support Engineer",
    "Product Manager", "Software Architect", "Hardware Engineer", "FPGA Engineer", "VLSI Designer",
    "Sales Engineer", "UI/UX Designer", "בודק/ת תוכנה", "מפתח תוכנה בכיר",
    "Marketing Analyst",          # no target keyword
])
def test_excluded_titles_fail(rules, title):
    assert not rules.check_title(title).ok, title


def test_it_is_case_sensitive(rules):
    # the word "it" inside a title must not trigger the IT exclusion
    assert rules.check_title("Software Engineer - Make it Fast").ok
    assert not rules.check_title("IT Software Engineer").ok


def test_lead_does_not_match_leading(rules):
    assert rules.check_title("Software Engineer, Leading-Edge AI").ok


@pytest.mark.parametrize("loc,workplace", [
    ("Haifa, Israel", ""), ("Israel, Yokneam", ""), ("Yokne'am Illit, Israel", ""),
    ("Migdal Ha'Emek", ""), ("MATAM, Haifa", ""), ("Nof HaGalil", ""), ("Kiryat Bialik", ""),
    ("IL-Haifa", ""), ("Caesarea, Israel", ""), ("Tel Aviv, Israel", "hybrid"), ("Israel - Remote", ""),
    ("חיפה", ""), ('מת"ם, חיפה', ""), ("Karmiel", ""), ("Misgav, Israel", ""),
])
def test_locations_pass(rules, loc, workplace):
    assert rules.check_location(job(location=loc, workplace=workplace)).ok, loc


@pytest.mark.parametrize("loc", [
    "Tel Aviv, Israel", "Petah Tikva, Israel", "Jerusalem, Israel", "Kiryat Gat, Israel",
    "Santa Clara, CA", "Remote - US", "", "Israel",
])
def test_locations_fail(rules, loc):
    assert not rules.check_location(job(location=loc)).ok, loc


def test_country_only_override(rules):
    assert rules.check_location(job(location="Israel"), {"country_only_ok": True}).ok


def test_workday_multi_location_defers(rules):
    j = job(location="3 Locations")
    j.extra["multi_location"] = True
    assert rules.check(j).ok
