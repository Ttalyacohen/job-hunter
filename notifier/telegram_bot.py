"""Telegram Bot API notifier: formatted job alert + tailored PDF attachment."""
from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

import httpx

from core.models import Evaluation, Job

log = logging.getLogger(__name__)

TRACK_BADGES = {
    "BACKEND_FULLSTACK": "🧩 Backend / Full-Stack",
    "AI_ML_RESEARCH": "🧠 AI / ML Research",
    "SYSTEMS_LOWLEVEL_CYBER": "🛡️ Systems / Low-Level / Cyber",
}


class TelegramError(RuntimeError):
    pass


def _location_line(job: Job, ev: Evaluation) -> str:
    listed = job.location or "Location not stated"
    actual = (ev.work_location or "").strip()
    if actual and actual.lower() not in listed.lower():
        return f"{actual} (board lists: {listed})"
    return listed


def format_alert(job: Job, ev: Evaluation) -> str:
    e = html.escape
    badge = TRACK_BADGES.get(ev.track, ev.track)
    score = f"{ev.match_score}/10" if ev.screened else "not screened"
    lines = [
        f"🚀 <b>{e(job.company)}</b> — <b>{e(job.title)}</b>",
        f"{badge}",
        f"📍 {e(_location_line(job, ev))}"
        + (f" · {e(job.workplace)}" if job.workplace else "")
        + f"   ⭐ <b>{score}</b>",
    ]
    if ev.years_required is not None:
        lines.append(f"🎓 Requires {ev.years_required}+ yrs")
    if ev.missing_requirements:
        lines.append("⚠️ <b>Gaps:</b> " + e("; ".join(ev.missing_requirements)))
    if ev.summary_bullets:
        lines.append("")
        lines += [f"• {e(b)}" for b in ev.summary_bullets[:3]]
    if not ev.screened:
        lines += ["", "⚠️ <i>Not AI-screened (Gemini quota exhausted) — check seniority yourself.</i>"]
    lines += ["", f'🔗 <a href="{e(job.url, quote=True)}">Apply here</a>']
    text = "\n".join(lines)
    return text[:4000]


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60))

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, *, data: dict, files: dict | None = None) -> dict:
        for attempt in range(4):
            try:
                resp = await self.client.post(f"{self.base}/{method}", data=data, files=files)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise TelegramError(f"{method}: network error {exc}") from exc
                await asyncio.sleep(2 * (attempt + 1))
                continue
            payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 200 and payload.get("ok"):
                return payload["result"]
            if resp.status_code == 429:
                retry = (payload.get("parameters") or {}).get("retry_after", 5)
                await asyncio.sleep(min(int(retry) + 1, 60))
                continue
            if resp.status_code >= 500 and attempt < 3:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise TelegramError(f"{method}: HTTP {resp.status_code} {payload.get('description', resp.text[:200])}")
        raise TelegramError(f"{method}: gave up after retries")

    async def send_message(self, text: str) -> dict:
        return await self._call("sendMessage", data={
            "chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })

    async def send_document(self, path: Path, caption: str = "", reply_to: int | None = None) -> dict:
        data = {"chat_id": self.chat_id, "caption": caption[:1000], "parse_mode": "HTML"}
        if reply_to:
            data["reply_to_message_id"] = str(reply_to)
        with open(path, "rb") as fh:
            return await self._call("sendDocument", data=data,
                                    files={"document": (path.name, fh.read(), "application/pdf")})

    async def send_job(self, job: Job, ev: Evaluation, pdf: Path | None) -> None:
        msg = await self.send_message(format_alert(job, ev))
        if pdf is not None:
            caption = f"📄 Tailored CV — {html.escape(job.company)} · {html.escape(job.title)}"
            await self.send_document(pdf, caption=caption, reply_to=msg.get("message_id"))
