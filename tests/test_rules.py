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
    "Physical Design Backend Engineer", "ASIC Software Engineer", "Research Scientist Student",
    "Software Engineer Intern", "סטודנט/ית לפיתוח תוכנה",
])
def test_excluded_titles_fail(rules, title):
    assert not rules.check_title(title).ok, title


def test_it_is_case_sensitive(rules):
    # the word "it" inside a title must not trigger the IT exclusion
    assert rules.check_title("Software Engineer - Make it Fast").ok
    assert not rules.check_title("IT Software Engineer").ok


def test_lead_does_not_match_leading(rules):
    assert rules.check_title("Software Engineer, Leading-Edge AI").ok


def test_intern_does_not_match_internal(rules):
    assert rules.check_title("Software Engineer, Internal Tools").ok


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


# ---- centre-of-Israel near train: kept only with hybrid / remote evidence ----
@pytest.fixture
def center_rules(filters):
    return RuleFilter(filters, center_hybrid=True)


def test_center_without_description_waits_for_details(center_rules):
    j = job(location="Tel Aviv, Israel")
    assert center_rules.check(j).ok                       # provisional, description not fetched yet
    assert not center_rules.check(j, strict=True).ok      # after enrich: still no evidence -> drop


@pytest.mark.parametrize("desc,ok", [
    ("We work in a hybrid model, 3 days a week in the office.", True),
    ("Remote-friendly team.", True),
    ("משרה היברידית, יומיים מהבית", True),
    ("On-site position, 5 days a week in our Tel Aviv office.", False),
])
def test_center_decided_by_description(center_rules, desc, ok):
    j = job(location="Ramat Gan, Israel")
    j.description = desc
    assert center_rules.check(j).ok is ok


def test_center_hybrid_flag_from_ats(center_rules):
    assert center_rules.check(job(location="Herzliya", workplace="hybrid")).ok


def test_non_train_city_still_dropped(center_rules):
    assert not center_rules.check(job(location="Jerusalem, Israel", workplace="")).ok
    assert not center_rules.check(job(location="Rehovot, Israel")).ok
