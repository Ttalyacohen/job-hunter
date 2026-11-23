"""Prompt + response schema for stage-2 evaluation.

Gemini never writes CV text. It returns IDs that the CV generator resolves
against data/profile.yaml, so the CV cannot contain fabricated facts.
"""
from __future__ import annotations

import json

from core.models import TRACKS, Job

ROLE_TYPES = ["SOFTWARE_DEV", "RESEARCH", "QA_TEST", "DEVOPS_IT_SUPPORT", "HARDWARE", "MANAGEMENT", "OTHER"]

SYSTEM_RULES = """You screen job postings for one specific candidate and plan CV tailoring.

HARD RULES
- Accept ONLY hands-on software development, engineering, or R&D/research roles (including Software Engineering, Backend/Full-Stack, Systems/Cyber, Algorithm Development, ML/AI Engineering, and AI/Cyber Research). Strictly REJECT any QA, software testing, test automation, verification, IT, support, or roles requiring >{max_years} years of experience.
- Also reject: DevOps-only, NOC/SOC, hardware design/verification, product/project/program management, sales, marketing, and any role that is mainly people management.
- years_required = the MINIMUM years of professional experience the posting requires (null if not stated). Academic research / M.Sc. thesis work does not count as professional years unless the posting says so. "3+ years" -> 3. "Preferred"/"advantage" years do not count as required.
- location_ok = false if the posting requires on-site work outside Northern Israel (e.g. "on-site in Tel Aviv / Petah Tikva / Jerusalem / Herzliya") with no hybrid or remote option. Northern Israel = Haifa, MATAM, Yokneam, Caesarea, Krayot, Karmiel, Migdal HaEmek, Nazareth / Nof HaGalil, Acre, Nahariya, Galilee. Hybrid or remote within Israel is OK.
- is_relevant = true ONLY if: role_type is SOFTWARE_DEV or RESEARCH, NOT QA/testing, match_score >= {min_score}, years_required is null or <= {max_years}, location_ok is true, and it fits a full-time M.Sc. Computer Science candidate.

SCORING (match_score 1-10): 9-10 = junior/new-grad dev or research role whose core stack matches the candidate's verified projects/courses; 7-8 = good fit with some gaps; 5-6 = plausible but weak overlap or borderline seniority; 1-4 = poor fit or excluded role type.

TRACK: AI_ML_RESEARCH (ML/AI/CV/NLP/LLM engineering or research, data science, algorithm research), SYSTEMS_LOWLEVEL_CYBER (C/C++, embedded/firmware, Linux, OS, networking, security/cyber research), BACKEND_FULLSTACK (backend, full-stack, frontend, general software engineering, Java/Python services).

OUTPUT FIELDS
- summary_bullets: exactly 3 short bullets (max 25 words each). Bullets 1 and 2 describe ONLY the job posting, never the candidate: (1) the posting's tech stack / research focus, (2) the posting's main responsibilities. Bullet 3 = why it fits, citing ONLY facts from the CANDIDATE FACTS below. Never invent experience.
- Student, intern and part-time positions do not fit (the candidate wants full-time work): set is_relevant false.
- tailoring_plan uses ONLY the IDs listed below:
  - subtitle_id: one of SUBTITLE IDS
  - domain_line_id: one of DOMAIN LINE IDS (the profile line closest to the role)
  - project_order: ALL project IDs, most relevant first
  - course_ids: the 6-8 most relevant course IDs, most relevant first
  - focus_skills: up to 6 skills from SKILLS that the posting asks for (exact strings)
  - keywords: up to 6 short terms from the posting that ALSO appear in the candidate facts
- rejection_reason: one short sentence when is_relevant is false, else "".
Return one evaluation per job, using its job_ref."""


def candidate_block(profile: dict) -> str:
    projects = []
    for p in profile["projects"]:
        bullets = p["bullets"].get("systems") or p["bullets"].get("ai") or []
        projects.append(f"- [{p['id']}] {p['title']}: " + " ".join(b.strip() for b in bullets))
    courses = ", ".join(f"[{c['id']}] {c['name']} ({c['grade']})" for c in profile["courses"])
    edu = "; ".join(f"{e['degree']}, {e['school']} ({e['years']}, {e['details']})" for e in profile["education"])
    ent = profile.get("entrepreneurship") or {}
    return "\n".join([
        "CANDIDATE FACTS (verified — the only facts you may cite)",
        f"Name: {profile['name']} | Location: {profile['contact']['location']} | {profile['availability']}",
        f"Education: {edu}",
        "Projects:", *projects,
        f"Entrepreneurship: {ent.get('title', '')} — {ent.get('meta', '')}. {ent.get('text', '')}".strip(),
        f"Courses: {courses}",
        f"Skills: {', '.join(all_skills(profile))}",
        "",
        f"SUBTITLE IDS: {', '.join(profile['subtitles'])}",
        f"DOMAIN LINE IDS: {', '.join(profile['profile']['domain_lines'])}",
        f"PROJECT IDS: {', '.join(p['id'] for p in profile['projects'])}",
    ])


def all_skills(profile: dict) -> list[str]:
    seen: dict[str, None] = {}
    for rows in profile["skills"].values():
        for row in rows:
            for s in row["items"]:
                seen.setdefault(s, None)
    return list(seen)


def jobs_block(jobs: list[tuple[str, Job]], max_chars: int) -> str:
    out = []
    for ref, j in jobs:
        desc = (j.description or "(no description available — judge from title and location)")[:max_chars]
        out.append(json.dumps({
            "job_ref": ref, "company": j.company, "title": j.title,
            "location": j.location, "workplace": j.workplace or "unspecified",
            "description": desc,
        }, ensure_ascii=False))
    return "JOBS (JSON lines):\n" + "\n".join(out)


def response_schema(profile: dict) -> dict:
    """OpenAPI-subset schema accepted by generationConfig.responseSchema."""
    def enum_str(values):
        return {"type": "STRING", "enum": list(values)}

    return {
        "type": "OBJECT",
        "properties": {
            "evaluations": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "job_ref": {"type": "STRING"},
                        "role_type": enum_str(ROLE_TYPES),
                        "years_required": {"type": "INTEGER", "nullable": True},
                        "location_ok": {"type": "BOOLEAN"},
                        "match_score": {"type": "INTEGER"},
                        "track": enum_str(TRACKS),
                        "is_relevant": {"type": "BOOLEAN"},
                        "rejection_reason": {"type": "STRING"},
                        "summary_bullets": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "tailoring_plan": {
                            "type": "OBJECT",
                            "properties": {
                                "subtitle_id": enum_str(profile["subtitles"].keys()),
                                "domain_line_id": enum_str(profile["profile"]["domain_lines"].keys()),
                                "project_order": {"type": "ARRAY", "items": enum_str(p["id"] for p in profile["projects"])},
                                "course_ids": {"type": "ARRAY", "items": enum_str(c["id"] for c in profile["courses"])},
                                "focus_skills": {"type": "ARRAY", "items": enum_str(all_skills(profile))},
                                "keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
                            },
                            "required": ["subtitle_id", "domain_line_id", "project_order", "course_ids",
                                         "focus_skills", "keywords"],
                        },
                    },
                    "required": ["job_ref", "role_type", "years_required", "location_ok", "match_score", "track",
                                 "is_relevant", "rejection_reason", "summary_bullets", "tailoring_plan"],
                },
            }
        },
        "required": ["evaluations"],
    }
