# 🛰️ Northern Israel Job Hunter

A zero-cost, always-on job hunting bot. Every 10 minutes GitHub Actions scans the career
boards of tech companies in Northern Israel, keeps only hands-on **software development /
R&D / research** roles, screens them with **Google Gemini**, builds a **tailored 1-page PDF CV**,
and sends both to you on **Telegram**.

```
companies.yaml ─► scraper/ ──► matcher/rules.py ──► matcher/gemini.py ──► cv_generator/ ──► notifier/
 (~30 → 200)     Greenhouse     Stage 1: title +     Stage 2: batched     strict 1-page    Telegram alert
                 Lever, Comeet  location rules       screening, score,    PDF from the     + PDF as reply
                 SmartRecruit.  (zero API cost)      track, tailoring     verified fact
                 Workable,                           plan (IDs only)      base
                 Workday, Amazon,
                 generic HTML
                         └──────────── data/seen_jobs.json (dedupe, committed by Actions) ────────────┘
```

## Contents
1. [Quick start (≈15 min)](#quick-start)
2. [Public or private repository?](#public-or-private-repository)
3. [How filtering works](#how-filtering-works)
4. [How the CV stays truthful](#how-the-cv-stays-truthful)
5. [Gemini models & free quota](#gemini-models--free-quota)
6. [Adding companies (towards 200)](#adding-companies-towards-200)
7. [Running locally](#running-locally)
8. [Configuration reference](#configuration-reference)
9. [Troubleshooting & limits](#troubleshooting--limits)

---

## Quick start

### 1. Create the repository
Create a new GitHub repository and push this folder to it (see the next section for public vs private).

### 2. Create the Telegram bot
1. In Telegram, open **@BotFather** → `/newbot` → pick a name → copy the **bot token**.
2. Send any message (e.g. `hi`) to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy
   `"chat":{"id": …}` — that number is your **chat id**.

### 3. Get a Gemini API key
Create a key at **Google AI Studio** (aistudio.google.com → *Get API key*). No billing needed.
In AI Studio's rate-limit page, check which Flash / Flash-Lite models your free project can use
(see [Gemini models](#gemini-models--free-quota)).

### 4. Add the secrets
Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `GEMINI_API_KEY` | your AI Studio key |
| `TELEGRAM_BOT_TOKEN` | token from BotFather |
| `TELEGRAM_CHAT_ID` | your chat id |
| `CANDIDATE_PROFILE_B64` | *(public repos only)* output of `python scripts/encode_profile.py` |

Then **Settings → Actions → General → Workflow permissions → Read and write permissions**
(the scanner commits `data/seen_jobs.json`).

### 5. First runs
1. **Actions → "Discover ATS & check sources" → Run workflow.** This detects the real hiring
   platform behind every `ats: unknown` company, rewrites `data/companies.yaml`, and prints a
   table of how many jobs each board returns. Fix or disable any board still showing `0 jobs`.
2. **Actions → "Job Scanner" → Run workflow** with *Dry run* ticked. Check the log.
3. Run it again without *Dry run*. You should get your first Telegram alerts. After that the
   10-minute schedule runs on its own.

> **First run behaviour.** With `first_sighting: queue` (default) the jobs that are already open
> are screened gradually, newest first, within the Gemini budget, because they are just as
> relevant to you as tomorrow's postings. Set `FIRST_SIGHTING=silent` (repository variable)
> if you only want postings published from now on.

---

## Public or private repository?

| | Public repo | Private repo (GitHub Free) |
|---|---|---|
| Actions minutes | **Free, unlimited** on standard runners | 2,000 min / month |
| Every-10-min schedule | ✅ free | ❌ ~4,300 runs/month × ≥1 billed min each → over quota |
| Your phone / email | keep them out of git: use the `CANDIDATE_PROFILE_B64` secret | can stay in `data/profile.yaml` |

**Recommended: public repository + profile in a secret.**

```bash
python scripts/encode_profile.py | gh secret set CANDIDATE_PROFILE_B64   # or paste it in the UI
echo "data/profile.yaml" >> .gitignore
git rm --cached data/profile.yaml && git commit -m "keep profile out of git"
```
Keep `data/profile.yaml` locally (for editing and local runs) and re-run the encode command
whenever you change it. Generated PDFs are never committed; they only go to Telegram.

If you prefer a private repo, change the cron to `'0 * * * *'` (hourly ≈ 720 runs × ~2 min
≈ 1,440 min/month) to stay within the free allowance.

---

## How filtering works

**Stage 1: rules, zero API cost** (`config/filters.yaml`, `matcher/rules.py`)
- **Title must contain a target** (Software Engineer/Developer, Backend, Full Stack, Algorithm,
  ML/AI/Research Engineer, Data Scientist, Security Researcher, Embedded, New Grad, Hebrew
  equivalents such as `מפתח/ת תוכנה`, `אלגוריתמאי/ת`, …).
- **Title must not contain an exclusion**: seniority (Senior, Sr, Staff, Principal, Lead, Manager,
  Director, VP, Architect, Head of), QA/testing/verification, DevOps, IT/NOC/SOC, support,
  product/project management, sales/marketing, UI/UX, hardware/VLSI/FPGA, and Hebrew equivalents.
  Matching is whole-word, so "Leading-edge" is not "Lead". `IT`, `SOC`, `NOC` and `HR` are
  case-sensitive, so "make **it** fast" is not "IT".
- **Location** is North (Haifa, MATAM, Yokneam, Caesarea, Krayot, Karmiel, Migdal HaEmek,
  Nazareth/Nof HaGalil, Acre, Nahariya, Galilee, Misgav/Teradion, Tefen, …, in English and
  Hebrew, spelling-tolerant), or **hybrid/remote in Israel**. Kiryat Gat / Kiryat Ono are *not*
  matched (only specific Krayot towns are listed).

**Stage 2: Gemini** (`matcher/prompt.py`, `matcher/gemini.py`)
- Up to 5 jobs are screened **per request** to stretch the free quota.
- The prompt contains your hard rule verbatim ("Accept ONLY hands-on software development …
  Strictly REJECT any QA … or roles requiring >3 years of experience").
- Gemini returns `match_score`, `track`, `is_relevant`, `years_required`, `role_type`,
  `location_ok`, `summary_bullets` and a `tailoring_plan`, constrained by a JSON response schema.
- **The code then re-checks every rule itself.** A job is only sent if role type is dev/research,
  score ≥ 7, required years ≤ 3 and the location is workable, even if the model said "relevant".

## How the CV stays truthful
Gemini **never writes CV text.** `data/profile.yaml` is the single source of every sentence on the
CV (taken from your two existing CVs and the verified fact base). Gemini only returns **IDs**:
which subtitle, which profile line, the order of projects, which courses, which of *your* skills to
bold. The generator discards any ID that is not in the file, and a test checks that every word on a
generated PDF exists in the fact base.

Per job it tailors exactly:
1. the **header subtitle** (5 options in `profile.yaml → subtitles`),
2. the **3-line Professional Profile** (education + the domain line closest to the role + MedFlow /
   immediate availability),
3. **project order** and **selected courses**, and **bold emphasis** on matching skills/keywords.

Two templates: `templates/base_systems_backend.html` (Backend/Full-Stack and Systems/Cyber tracks;
skills first) and `templates/base_ai_research.html` (AI/ML/Research; research first). Output is a
strict single A4 page: if content overflows, the generator **cuts** lower-priority content (extra
courses, then extra bullets of the least relevant projects, then whole projects) and never shrinks
the type. File name: `CV_Talya_Cohen_<Company>.pdf`.

Render previews any time: `python main.py --render-samples` → `output/samples/`.

---

## Gemini models & free quota

Google changes model names and free-tier limits often. As of November 2026 its deprecation
page lists conflicting shutdown information for `gemini-2.5-flash` and recommends newer
Flash / Flash-Lite models for new projects, and free-tier daily requests for Flash have been
reduced for many accounts. The bot is built for this:

- **Fallback chain** (`config/settings.yaml → gemini.models`, or repository variable
  `GEMINI_MODELS=model-a,model-b`). On **404** (retired) the model is skipped permanently; on a
  **daily-quota 429** it is skipped until midnight Pacific; on a **per-minute 429** it waits and retries.
  Quotas are per model, so a chain also multiplies daily capacity.
- **Batching** (5 jobs/request) and **pacing** (≥7 s between calls).
- **Nothing is lost when quota runs out**: unscreened candidates stay queued and are screened on the
  next run. Set the repository variable `ON_QUOTA_EXHAUSTED=notify_unscreened` if you would rather
  get an immediate, clearly-marked "not AI-screened" alert with a keyword-chosen CV.
- If **every** model returns 404 you get one Telegram warning per day telling you to update `GEMINI_MODELS`.

Default chain: `gemini-2.5-flash, gemini-3.5-flash-lite, gemini-3.8-flash`. Replace it with the
models your AI Studio rate-limit page shows as available on the free tier.

---

## Adding companies (towards 200)

`data/companies.yaml` ships with 32 Northern-Israel-relevant employers. Mobileye's Lever board
(`jobs.eu.lever.co/mobileye`, MATAM roles) is confirmed. The rest are marked `verified: false`:
Workday tenants are best-known values, and companies marked `ats: unknown` are scraped as generic
HTML until discovery finds their ATS. Run the **Discover ATS** workflow once to resolve them.

To add many companies at once, create a text file with `Company Name, https://careers-page`
per line and run:

```bash
python scripts/discover_ats.py --from-file new_companies.txt --append
python main.py --check-sources          # health table: which boards return jobs
```

`discover_ats.py` scans the careers page (and its iframes) for Greenhouse, Lever, Comeet,
SmartRecruiters, Workable and Workday URLs or widget config, falls back to probing the public APIs
with slug guesses, and **validates each hit by fetching the real job list** before writing it.

Entry formats:

```yaml
- {name: Acme, ats: greenhouse, board: acme}
- {name: Acme, ats: lever, site: acme, region: eu}          # region only for jobs.eu.lever.co
- {name: Acme, ats: comeet, uid: "AB.123", token: "…"}      # or uid + slug (hosted page)
- {name: Acme, ats: smartrecruiters, company_id: Acme}
- {name: Acme, ats: workable, account: acme}
- {name: Acme, ats: workday, host: acme.wd5.myworkdayjobs.com, tenant: acme, site: External, search_texts: [Israel]}
- {name: Acme, ats: generic, careers_url: "https://…", link_pattern: "/jobs/\\d+", default_location: "Haifa, Israel"}
```
Optional on any entry: `enabled: false`, `country_only_ok: true` (accept location "Israel" with no city).

Performance: requests share one `asyncio.Semaphore(15)` with random jitter and rotating
User-Agents. A simulated scan of 200 boards (including 15 heavily paginated Workday tenants,
0.3–1.5 s latency per request) completes in ~36 s.

---

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# WeasyPrint needs Pango: Ubuntu `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`
#                         macOS  `brew install pango`

export GEMINI_API_KEY=… TELEGRAM_BOT_TOKEN=… TELEGRAM_CHAT_ID=…
python main.py --check-sources       # board health table
python main.py --render-samples      # preview CVs for all three tracks
python main.py --test-telegram       # sends a test alert + sample CV
python main.py --dry-run -v          # full pipeline, prints alerts, writes PDFs to output/, no state saved
python main.py --companies NVIDIA,Mobileye --dry-run    # debug a few boards
pytest -q                            # 92 offline tests (all HTTP mocked)
```

---

## Configuration reference

| File | What |
|---|---|
| `config/settings.yaml` | concurrency, timeouts, Gemini models/batching/budget, relevance thresholds, first-run mode |
| `config/filters.yaml` | title targets/exclusions and location keywords (edit freely) |
| `data/companies.yaml` | boards to scan |
| `data/profile.yaml` | the CV fact base (only source of CV text) |
| `data/seen_jobs.json` | handled job keys: `n` notified, `r` rejected by Gemini, `f` filtered by rules, `b` bootstrap, `o` delivery pending |
| `data/state.json` | Gemini per-model daily usage, exhausted/retired models, Telegram outbox |

Repository **variables** (Settings → Variables) override settings without a commit:
`GEMINI_MODELS`, `ON_QUOTA_EXHAUSTED` (`wait` / `notify_unscreened`), `FIRST_SIGHTING` (`queue` / `silent`).

To get an alert again for a job, delete its line from `data/seen_jobs.json`.

---

## Troubleshooting & limits

- **Runs are not exactly every 10 minutes.** GitHub's scheduler is best-effort and commonly delays
  cron runs by several minutes at busy times.
- **Scheduled runs stopped.** In public repos GitHub disables schedules after 60 days without
  repository activity. The bot normally commits state at least daily (last-seen dates), which
  counts as activity; if it ever happens, open Actions → Job Scanner → *Enable workflow*.
- **A board shows `0 jobs` in `--check-sources`.** Wrong identifier, or a JavaScript-rendered page
  the generic scraper can't read (Google, Apple and Microsoft careers sites are like this). Run
  discovery; if no ATS is found, keep that company's own job-alert emails as a complement.
- **Workday returns 4xx.** Tenant/site names are case-sensitive; open the company's
  `*.myworkdayjobs.com` page and copy them from the URL.
- **Gemini 403 / auth error.** The key is wrong or restricted; the run logs it and keeps jobs queued.
- **Telegram fails.** The alert is kept in an outbox and retried on the next runs (up to 5 times).
- **Duplicate alerts never happen across runs**: dedupe is by `ats:company:job_id`, runs never
  overlap (workflow `concurrency`), and state is committed after every run.
