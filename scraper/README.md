# Adapters

| ats | endpoint | config keys |
|---|---|---|
| greenhouse | `GET boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` | `board` |
| lever | `GET api.lever.co/v0/postings/{site}?mode=json` (EU: `api.eu.lever.co`) | `site`, `region: eu` |
| comeet | `GET comeet.co/careers-api/2.0/company/{uid}/positions?token=…&details=true`, or hosted page `comeet.com/jobs/{slug}/{uid}` | `uid` + `token`, or `uid` + `slug` |
| smartrecruiters | `GET api.smartrecruiters.com/v1/companies/{company_id}/postings` | `company_id`, `country` (default `il`) |
| workable | `GET apply.workable.com/api/v1/widget/accounts/{account}?details=true` | `account` |
| workday | `POST {host}/wday/cxs/{tenant}/{site}/jobs`, filtered by the site's own location facet | `host`, `tenant`, `site`, `location_terms` (default `[Israel]`), fallback `search_texts` |
| eightfold | `GET {host}/api/pcsx/search` or `/api/apply/v2/jobs` (Qualcomm, Microsoft) | `host`, `domain`, `locations` |
| breezy | `GET {subdomain}.breezy.hr/json` | `subdomain` |
| amazon | `GET amazon.jobs/en/search.json?loc_query=…` | `loc_queries` |
| generic / unknown | HTML + JSON-LD scrape of `careers_url` | `careers_url`, `link_pattern`, `default_location` |

Each adapter returns `core.models.Job` objects. `enrich()` is called only for
jobs that pass stage 1, to fetch the full description (Workday, SmartRecruiters,
generic) without paying for it on every posting.
