"""Multi-track dynamic CV generator (HTML/CSS -> strict 1-page PDF via WeasyPrint).

Tailoring is limited to exactly what the spec allows:
  1. header subtitle            (chosen from profile.subtitles)
  2. 3-line Professional Profile (education + chosen domain line + closing line)
  3. order / emphasis of Projects and Selected Courses (+ bold focus skills / keywords)
All text comes from data/profile.yaml; Gemini only supplies IDs.
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from core.config import TEMPLATES_DIR
from core.models import Evaluation, Job

log = logging.getLogger(__name__)

TRACK_TEMPLATE = {
    "AI_ML_RESEARCH": ("base_ai_research.html", "ai"),
    "BACKEND_FULLSTACK": ("base_systems_backend.html", "systems"),
    "SYSTEMS_LOWLEVEL_CYBER": ("base_systems_backend.html", "systems"),
}
TRACK_DEFAULTS = {  # subtitle id, domain line id
    "AI_ML_RESEARCH": ("ai_research", "ai"),
    "BACKEND_FULLSTACK": ("backend", "backend"),
    "SYSTEMS_LOWLEVEL_CYBER": ("systems", "systems"),
}


def safe_filename_part(s: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE).strip("_")
    return re.sub(r"_+", "_", s)[:60] or "Company"


def bold_keywords(text: str, keywords: list[str]) -> Markup:
    """Escape text, then bold the first occurrence of each keyword (whole word).
    Only emphasises words that are already in the verified text."""
    out = str(escape(text))
    for kw in keywords:
        kw = kw.strip()
        if len(kw) < 2:
            continue
        rx = re.compile(rf"(?<![\w>]){re.escape(str(escape(kw)))}(?![\w<])", re.I)
        out = rx.sub(lambda m: f"<strong>{m.group(0)}</strong>", out, count=1)
    return Markup(out)


@dataclass
class CVPlan:
    template: str
    variant: str
    subtitle_id: str
    domain_line_id: str
    project_order: list[str]
    course_ids: list[str]
    focus_skills: list[str]
    keywords: list[str]


class CVGenerator:
    def __init__(self, profile: dict, cv_settings: dict, templates_dir: Path = TEMPLATES_DIR):
        self.profile = profile
        self.settings = cv_settings
        self.env = Environment(loader=FileSystemLoader(str(templates_dir)),
                               autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)
        self.css = (templates_dir / "cv.css").read_text(encoding="utf-8")

    # ---- planning ------------------------------------------------------------
    def plan(self, track: str, tailoring: dict | None) -> CVPlan:
        tailoring = tailoring or {}
        template, variant = TRACK_TEMPLATE.get(track, TRACK_TEMPLATE["BACKEND_FULLSTACK"])
        defaults = self.profile["defaults"][variant]
        sub_default, dom_default = TRACK_DEFAULTS.get(track, TRACK_DEFAULTS["BACKEND_FULLSTACK"])

        all_projects = [p["id"] for p in self.profile["projects"]]
        order = [p for p in tailoring.get("project_order") or [] if p in all_projects]
        order += [p for p in defaults["project_order"] if p not in order]
        order += [p for p in all_projects if p not in order]

        valid_courses = {c["id"] for c in self.profile["courses"]}
        courses = [c for c in tailoring.get("course_ids") or [] if c in valid_courses]
        if len(courses) < 6:   # top up with the template's defaults
            courses += [c for c in defaults["courses"] if c not in courses]
        courses = courses[: self.settings.get("max_courses", 8)]

        sub = tailoring.get("subtitle_id")
        dom = tailoring.get("domain_line_id")
        return CVPlan(
            template=template, variant=variant,
            subtitle_id=sub if sub in self.profile["subtitles"] else sub_default,
            domain_line_id=dom if dom in self.profile["profile"]["domain_lines"] else dom_default,
            project_order=order, course_ids=courses,
            focus_skills=list(tailoring.get("focus_skills") or []),
            keywords=list(tailoring.get("keywords") or []),
        )

    # ---- context -------------------------------------------------------------
    def context(self, plan: CVPlan) -> dict:
        p = self.profile
        by_id = {x["id"]: x for x in p["projects"]}
        projects = []
        for pid in plan.project_order:
            proj = by_id[pid]
            bullets = proj["bullets"].get(plan.variant) or proj["bullets"].get("systems") or []
            title = proj.get(f"title_{plan.variant}") or proj["title"]
            projects.append({
                "id": pid, "title": title, "meta": proj.get("meta"), "link": proj.get("link"),
                "bullets": [bold_keywords(" ".join(b.split()), plan.keywords) for b in bullets],
            })
        course_by_id = {c["id"]: c for c in p["courses"]}
        focus = {s.lower() for s in plan.focus_skills}
        skills = [{"label": row["label"],
                   "entries": [{"name": s, "focus": s.lower() in focus} for s in row["items"]]}
                  for row in p["skills"][plan.variant]]
        return {
            "name": p["name"],
            "contact": p["contact"],
            "subtitle": p["subtitles"][plan.subtitle_id],
            "profile_lines": [" ".join(p["profile"]["education"].split()),
                              " ".join(p["profile"]["domain_lines"][plan.domain_line_id].split()),
                              " ".join(p["profile"]["closing"].split())],
            "projects": projects,
            "courses": [f"{course_by_id[c]['name']} ({course_by_id[c]['grade']})" for c in plan.course_ids],
            "skills": skills,
            "education": p["education"],
            "entrepreneurship": p.get("entrepreneurship"),
            "military": p.get("military", []),
            "volunteering": " ".join((p.get("volunteering") or "").split()),
        }

    def render_html(self, plan: CVPlan, ctx: dict | None = None) -> str:
        return self.env.get_template(plan.template).render(cv=ctx or self.context(plan), css=self.css)

    # ---- PDF with one-page enforcement ---------------------------------------
    def _shrink_steps(self, ctx: dict):
        """Yield progressively shorter contexts (cut content, never shrink type)."""
        c = copy.deepcopy(ctx)
        if len(c["courses"]) > 6:
            c["courses"] = c["courses"][:6]
            yield copy.deepcopy(c)
        # trim bullets from the least relevant project upwards (keep >= 1 bullet each)
        for proj in reversed(c["projects"][1:]):
            while len(proj["bullets"]) > 1:
                proj["bullets"].pop()
                yield copy.deepcopy(c)
        # drop least relevant projects, keep at least 3
        while len(c["projects"]) > 3:
            c["projects"].pop()
            yield copy.deepcopy(c)

    def render_pdf(self, plan: CVPlan, out_path: Path) -> Path:
        from weasyprint import HTML   # imported lazily: only needed when a CV is generated

        ctx = self.context(plan)
        candidates = [ctx, *self._shrink_steps(ctx)]
        doc = None
        for i, c in enumerate(candidates):
            doc = HTML(string=self.render_html(plan, c), base_url=str(TEMPLATES_DIR)).render()
            if len(doc.pages) <= 1:
                if i:
                    log.info("CV fitted to one page after %d content cut(s)", i)
                break
        else:
            log.warning("CV still exceeds one page after all cuts; writing first page only")
            doc = doc.copy(doc.pages[:1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc.write_pdf(str(out_path))
        return out_path

    def generate(self, job: Job, evaluation: Evaluation, out_dir: Path) -> Path:
        plan = self.plan(evaluation.track, evaluation.tailoring_plan)
        fname = f"CV_{safe_filename_part(self.profile['name'])}_{safe_filename_part(job.company)}.pdf"
        # one sub-directory per job so two roles at the same company never collide
        return self.render_pdf(plan, out_dir / safe_filename_part(job.job_id)[:40] / fname)
