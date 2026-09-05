# PROJECT AUDIT — Agent For Trend Scouting and Marketing

> Generated: 2026-09-04
> Scope: Working tree only (no git repository present — see Security section)

---

## 1. Security & Secrets Audit

### 1.1 Git Status

**No git repository exists.** This directory is a zip-extracted archive, not a git clone. There is no `.git` folder, no commit history, and no remotes configured. Therefore, git-history scanning for leaked secrets is **not applicable**.

### 1.2 `.env` and Secrets Files

| Item | Status |
|------|--------|
| `.env` file on disk | **Not present.** No `.env` file exists anywhere in the working tree. |
| `.gitignore` coverage | **Minimal.** The file exists but contains only a single entry: `.env`. It does **not** cover `.env.*`, `*.pem`, `*.key`, `credentials.json`, `service-account-*.json`, or any other common secret pattern. |
| `.gitignore` missing patterns | `*.env`, `.env.*`, `*.pem`, `*.p12`, `*.key`, `*secret*`, `*credentials*`, `venv/`, `agent/`, `__pycache__/`, `*.pyc`, `.DS_Store`, `node_modules/` |

### 1.3 Hardcoded Credentials — Working Tree Scan

| File | Line(s) | Finding | Severity |
|------|---------|---------|----------|
| `trend_scout.py` | 29–33 | Placeholder strings `YOUR_REDDIT_CLIENT_ID`, `YOUR_REDDIT_CLIENT_SECRET`, `YOUR_TWITTER_BEARER_TOKEN` | **None** — clearly placeholders, never held real values |
| `analyze_trends.py` | 173 | `os.environ.get("OPENAI_API_KEY", "")` — properly loaded from env via `python-dotenv` | **Good practice** |
| `parse_business.py` | 56 | `os.environ.get("OPENAI_API_KEY", "")` — same pattern | **Good practice** |
| All other `.py` files | — | No API keys, tokens, passwords, or auth headers found | **Clean** |
| All `.json` / `.html` / `.txt` files | — | No secrets detected | **Clean** |

**No real (non-placeholder) credentials exist anywhere in the working tree.** No key material matching common patterns (`sk-...`, `ghp_...`, `AIza...`, `xox*-...`, `glpat-...`) was found.

### 1.4 Information Leaks

| File | Line | What Leaked | Severity |
|------|------|-------------|----------|
| `business_profile.json` | 47 | Local path `/home/zaid/Agent_For_Trend_Scouting_and_Marketing/business_20260402_205355.txt` reveals developer username `zaid` and original Linux machine path | Low — informational |
| `trend_analysis_20260402_205438.json` | 158–159 | Same `/home/zaid/...` paths in `_meta` block | Low — informational |
| `agent/pyvenv.cfg` | 5 | Original venv creation path `/home/zaid/Agent_For_Trend_Scouting_and_Marketing/agent` | Low — informational |
| Entire `agent/` directory | — | The full Python virtual environment (Linux-built, ~3.12.3) is bundled in the archive. Should **never** be distributed or committed. | Medium — hygiene issue |

### 1.5 Action Items

- [ ] **Expand `.gitignore`** before initializing git. Add: `*.env`, `.env.*`, `agent/`, `venv/`, `__pycache__/`, `*.pyc`, `*.pem`, `*.key`, `.DS_Store`, `*secret*`, `*credentials*`
- [ ] **Strip local paths** from `business_profile.json` and existing `trend_analysis_*.json` files before sharing (replace `/home/zaid/...` with relative paths or remove `_source_file` / `_meta` fields)
- [ ] **Remove `agent/` directory** from any distributed archive — it's a Linux venv and non-functional on other machines
- [ ] **Rotate any OpenAI key** that was used during development if this archive was ever shared publicly, since the key itself isn't here but the usage context is

---

## 2. Tech Stack & Structure

### 2.1 Language & Runtime

- **Language:** Python 3.12 (venv was created on Linux with Python 3.12.3)
- **Web framework:** Flask 3.1.3
- **Frontend:** Single-page HTML/CSS/JS (no framework, no build step)
- **AI/LLM:** OpenAI GPT-4o via REST API (no SDK used; raw `requests.post`)
- **Data source:** Google Trends via `pytrends 4.9.2` (unofficial Python wrapper)

### 2.2 Directory Layout

```
Agent_For_Trend_Scouting_and_Marketing-main/
├── agent/                      # Python venv (LINUX — do not distribute)
├── static/
│   └── index.html              # 704-line SPA: form + processing + done screens
├── .gitignore                  # Only ignores `.env`
├── README.md                   # Empty (just the title)
├── app.py                      # Flask backend — main entry point for web UI
├── run.py                      # CLI pipeline runner (alternative entry point)
├── google_trends.py            # Fetches keyword-based Google Trends data
├── google_trending_now.py      # Fetches realtime trending searches from Google
├── analyze_trends.py           # GPT-4o analysis: maps trends → business strategy
├── parse_business.py           # GPT-4o parsing: free-text → structured business profile
├── generate_report.py          # Generates HTML report from analysis JSON
├── trend_scout.py              # Multi-source scout (Reddit/Twitter/RSS/IG) — UNUSED by pipeline
├── _tmp_gt.py                  # Leftover temp copy of google_trends.py — should be deleted
├── requirements.txt            # INCOMPLETE: only lists snscrape==0.7.0.20230622
├── my_business.txt             # Sample input: Velvet Crumbs bakery profile
├── business_*.txt              # 5 generated business input files (from UI submissions)
├── business_profile.json       # Parsed profile for "FirePit BBQ Co." (latest run)
├── google_trends_*.json        # 5 trend fetch outputs
├── google_trending_now_*.json  # 4 realtime trend outputs
├── trend_analysis_*.json       # 4 GPT-4o analysis outputs
├── trend_report_*.html         # 4 generated HTML reports
└── trends_*.csv                # 1 CSV from trend_scout.py standalone run
```

### 2.3 Entry Points

| Entry | How to Run | What It Does |
|-------|-----------|--------------|
| Web UI | `python3 app.py` → `http://localhost:5000` | Flask server; 4-step form → async pipeline → downloadable HTML report |
| CLI | `python3 run.py my_business.txt [--geo PK] [--keywords ...] [--skip-fetch]` | Runs the full pipeline from the command line |
| Standalone modules | Each `.py` file can be run individually with `python3 <file>.py` | Fetch, parse, analyze, or report in isolation |

---

## 3. Module-by-Module Breakdown

### `app.py` — Flask Backend (SOLID)

- Serves `static/index.html` at `/`
- `POST /api/submit`: accepts JSON business profile, writes it to a timestamped `.txt`, kicks off the pipeline in a daemon thread
- `GET /api/status/<job_id>`: returns current pipeline status + log
- `GET /api/report/<job_id>`: serves the finished HTML report file
- Pipeline runs 5 subprocess steps sequentially: `parse_business.py` → `google_trends.py` → `google_trending_now.py` → `analyze_trends.py` → `generate_report.py`
- Jobs are stored in an in-memory dict (`jobs = {}`). **No persistence** — lost on restart.

### `run.py` — CLI Orchestrator (SOLID)

- Same pipeline as `app.py` but invoked from command line
- Patches `google_trends.py` and `google_trending_now.py` source code inline (string replacement) to inject user-supplied keywords/geo — **fragile hack** that depends on exact string matching in source files
- Has `--skip-fetch` to reuse existing trend JSONs

### `google_trends.py` — Keyword Trend Fetcher (WORKING)

- Uses `pytrends` to fetch: interest over time, related queries (rising + top), related topics
- Accepts `--keywords` (max 5, Google's limit), `--geo`, `--timeframe`
- Defaults: Pakistan (`PK`), keywords `["PSL", "rain Karachi", "Eid", "drama Pakistan", "celebrity"]`, timeframe `now 7-d`
- Has retry logic with exponential backoff for 429 rate limits
- Includes deliberate `time.sleep()` delays between API calls to avoid rate limiting

### `google_trending_now.py` — Realtime Trend Fetcher (WORKING)

- Uses `pytrends.realtime_trending_searches(pn=geo)` — no keyword filter
- Extracts title, entity names, and related articles
- Simpler than `google_trends.py`, no retry logic

### `analyze_trends.py` — AI Analysis Layer (WORKING)

- Loads `business_profile.json` + latest trend JSONs
- Sends everything to GPT-4o with a detailed system prompt defining "trend hijacking" strategy
- System prompt instructs the LLM to find creative bridges between trending topics and the business
- Returns structured JSON with: trend analysis, product ideas, content ideas, relevance scores, timing, lifecycle, brand safety, top opportunities, skip list
- Requires `OPENAI_API_KEY` env var (loaded via `python-dotenv`)

### `parse_business.py` — Business Profile Parser (WORKING)

- Takes a free-text `.txt` business description
- Sends to GPT-4o with a schema-enforcing system prompt
- Extracts: business name, industry, products, target audience, brand tone, competitors, UVP, stage, geographic focus
- Outputs `business_profile.json`

### `generate_report.py` — HTML Report Generator (WORKING)

- Takes a `trend_analysis_*.json` and produces a styled, self-contained HTML report
- Sorts trends by timing priority (act now → this week → plan ahead → skip)
- Renders: top opportunities cards, skip list, per-trend cards with relevance dots, lifecycle pills, product ideas, content ideas, bridge explanations
- Uses inline CSS, Google Fonts (Cabinet Grotesk, JetBrains Mono)

### `trend_scout.py` — Multi-Source Scout (UNUSED BY PIPELINE)

- Standalone module that was likely the original prototype before the current pipeline
- Implements 6 data sources: Google Trends, Google Trending Searches, Reddit (PRAW), Twitter/X (Bearer token + tweepy), RSS feeds, Instagram hashtag scraping
- Reddit and Twitter require real API credentials (currently placeholder strings)
- Instagram scraping uses undocumented `?__a=1` endpoint (almost certainly broken)
- Outputs a combined CSV
- **Not called by `app.py` or `run.py` at all** — completely disconnected from the main pipeline

### `_tmp_gt.py` — Leftover Temp File

- Near-duplicate of `google_trends.py` with food-specific keywords hardcoded
- Should be deleted — artifact from a manual test run

---

## 4. Integrations Status

| Integration | Status | Notes |
|-------------|--------|-------|
| Google Trends (pytrends) | **Working** | Used by `google_trends.py` and `google_trending_now.py`. Subject to 429 rate limits; retry logic exists. |
| OpenAI GPT-4o | **Working** | Used by `parse_business.py` and `analyze_trends.py`. Requires `OPENAI_API_KEY`. |
| Reddit (PRAW) | **Not configured** | Placeholder credentials in `trend_scout.py`. Module not used by pipeline. |
| Twitter/X | **Not configured** | Placeholder Bearer token. Uses both v1.1 (trends) and v2 (search) APIs. Module not used by pipeline. |
| RSS Feeds | **Functional** | No credentials needed. Implemented in `trend_scout.py` only, not used by pipeline. |
| Instagram | **Likely broken** | Uses undocumented `?__a=1` endpoint that Instagram has restricted. Not used by pipeline. |
| snscrape | **Dead dependency** | Listed in `requirements.txt` but never imported anywhere. snscrape's Twitter/Reddit scrapers are largely broken since 2023 platform API changes. |

---

## 5. Dependencies — Installed in `agent/` Venv

The venv was built on Linux (Python 3.12.3). Packages actually installed:

| Package | Version | Used By |
|---------|---------|---------|
| flask | 3.1.3 | `app.py` |
| openai | 2.30.0 | *(installed but not imported — code uses raw `requests`)* |
| pytrends | 4.9.2 | `google_trends.py`, `google_trending_now.py`, `trend_scout.py` |
| python-dotenv | 1.2.2 | `analyze_trends.py`, `parse_business.py` |
| requests | 2.33.1 | All modules making HTTP calls |
| praw | 7.8.1 | `trend_scout.py` (Reddit) |
| tweepy | 4.16.0 | `trend_scout.py` (Twitter) |
| feedparser | 6.0.12 | `trend_scout.py` (RSS) |
| pandas | 3.0.2 | `trend_scout.py` |
| numpy | 2.4.4 | Transitive (pandas) |
| rich | 14.3.3 | `trend_scout.py` (console output) |
| beautifulsoup4 | 4.14.3 | Installed but not directly imported |
| lxml | 6.0.2 | Transitive (beautifulsoup4, feedparser) |
| snscrape | 0.7.0.20230622 | **Not imported anywhere** |
| werkzeug | 3.1.7 | Transitive (Flask) |
| jinja2 | 3.1.6 | Transitive (Flask) |
| httpx | 0.28.1 | Transitive (openai) |
| pydantic | 2.12.5 | Transitive (openai) |

### `requirements.txt` — INCOMPLETE

Only lists `snscrape==0.7.0.20230622`. Should list at minimum:
```
flask>=3.0
pytrends>=4.9
python-dotenv>=1.0
requests>=2.30
```

If `trend_scout.py` is kept, also add:
```
praw>=7.8
tweepy>=4.14
feedparser>=6.0
pandas>=2.0
rich>=13.0
beautifulsoup4>=4.12
```

---

## 6. Environment Variables & Setup

### Required

| Variable | Used In | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | `analyze_trends.py`, `parse_business.py` | OpenAI API authentication (GPT-4o calls) |

### How to Run

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# or: venv\Scripts\activate     # Windows

# 2. Install dependencies
pip install flask pytrends python-dotenv requests

# 3. Set your OpenAI key
export OPENAI_API_KEY="sk-..."  # Linux/Mac
# or: set OPENAI_API_KEY=sk-... # Windows

# 4a. Run via web UI
python3 app.py
# Open http://localhost:5000

# 4b. Or run via CLI
python3 run.py my_business.txt --geo PK --keywords "PSL,rain,Eid"
```

### Optional (not required for main pipeline)

| Variable | Used In | Purpose |
|----------|---------|---------|
| `REDDIT_CLIENT_ID` | `trend_scout.py` | Reddit API auth (module not in pipeline) |
| `REDDIT_CLIENT_SECRET` | `trend_scout.py` | Reddit API auth |
| `TWITTER_BEARER_TOKEN` | `trend_scout.py` | Twitter API v1.1/v2 auth (module not in pipeline) |

---

## 7. UI — What It Currently Does

The frontend (`static/index.html`) is a polished, fully functional single-page app with 4 screens:

1. **Form (4 steps):**
   - Step 1: Business name, tagline, description, products/services
   - Step 2: Target audience, USP, brand tone (chip selector)
   - Step 3: Location, business stage, marketing channels (chip selector), extra notes
   - Step 4: Country selector (PK/US/IN/GB/AE/Worldwide), trend keywords (free text, max 5)

2. **Processing:** Shows a spinner, rotating status messages, and a live log box that polls `GET /api/status/<job_id>` every 2 seconds

3. **Done:** Shows a "View Full Report" button linking to `GET /api/report/<job_id>`

4. **Report:** Self-contained styled HTML page with trend cards, opportunity rankings, content ideas, and skip list

**UI is production-quality for a prototype.** Design uses Cabinet Grotesk + JetBrains Mono fonts, grain texture overlay, responsive layout, and smooth transitions. No framework — pure vanilla JS.

**UI limitations:**
- No authentication or rate limiting on the API
- No error recovery (if pipeline fails, user must reload)
- Job state is in-memory only (restart = all jobs lost)
- No history/list of past analyses
- Geo defaults to Pakistan; keyword defaults are Pakistan-specific

---

## 8. TODOs, Known Issues, and Code Smells

### TODOs / FIXMEs in Code

**None found.** No `TODO`, `FIXME`, `HACK`, `XXX`, or `TEMP` comments exist in any project source file.

### Known Issues Identified

| Issue | Location | Severity |
|-------|----------|----------|
| `requirements.txt` only lists snscrape (which isn't even used) | `requirements.txt` | High — new developers can't reproduce the environment |
| `run.py` patches source code via string replacement to inject CLI args | `run.py:67-93` | High — breaks if source strings change even slightly |
| `trend_scout.py` is completely disconnected from the pipeline | `trend_scout.py` | Medium — 318 lines of unused code |
| `_tmp_gt.py` is a leftover temp file | `_tmp_gt.py` | Low — should be deleted |
| `agent/` venv is Linux-built and non-portable | `agent/` directory | Medium — should not be distributed |
| Flask runs with `debug=True` in production entry point | `app.py:183` | Medium — exposes debugger, should be `False` for any non-dev use |
| Job state is in-memory dict, not persisted | `app.py:59` | Medium — restarts lose all job data |
| No input validation on API endpoints | `app.py:139-159` | Low-Medium — accepts arbitrary JSON |
| Default keywords/geo are Pakistan-specific | `google_trends.py`, UI defaults | Low — configurable but not obvious |
| `openai` SDK is installed but code uses raw `requests` to call the API | `analyze_trends.py`, `parse_business.py` | Low — works but misses SDK benefits (retry, streaming, type safety) |

---

## 9. Alignment with Project Intent

The project's stated purpose has two hard parts. Here's how the code measures up:

### (a) Detecting *actually fresh* trends vs. stale ones

**The code does NOT solve this.** Both `google_trends.py` and `google_trending_now.py` return whatever `pytrends` gives them. There is:
- No acceleration/momentum detection (is a trend rising or just lingering?)
- No cross-source validation (is it trending in multiple places or just one?)
- No staleness filter (how old is this "trending" topic really?)
- The `trend_scout.py` module *could* have been this layer (multi-source), but it was never integrated

The "freshness" question is implicitly delegated to GPT-4o via the `lifecycle` field (`emerging | rising | peak | saturating | declining`) in the analysis prompt. The LLM guesses lifecycle stage from the trend name and context — it has no actual temporal data to make that judgment.

### (b) Matching trends to a specific business → ad creative

**This part works well.** The `analyze_trends.py` system prompt is thoughtfully engineered with the "trend hijacking" concept — finding emotional/behavioral bridges between unrelated trending topics and the business. The output includes specific product ideas, content ideas with ready-to-post captions, platform-specific formats, and brand safety flags. The sample outputs in `trend_analysis_*.json` show genuinely creative and actionable suggestions.

### Scope: "Food businesses only"

**The code has no food-business restriction.** Nothing in the pipeline enforces industry scope. The system prompt in `analyze_trends.py` is generic ("small businesses"). The sample data includes a bakery (`my_business.txt` → Velvet Crumbs) and a BBQ restaurant (`business_profile.json` → FirePit BBQ Co.), but the pipeline would work for any industry.

### Summary of Gaps vs. Intent

| Intent | Code Reality |
|--------|-------------|
| Detect fresh/accelerating trends | Returns raw pytrends data; LLM guesses lifecycle |
| Multi-source trend detection | Only Google Trends is wired in; Reddit/Twitter/RSS exist but unused |
| Food-business scoped | No industry restriction in code |
| Generate ad creatives | **Works** — GPT-4o produces specific, creative, actionable output |
| Match trend → business | **Works** — "trend hijacking" prompt engineering is solid |

---

## 10. Files That Should Be Cleaned Up

| File | Reason |
|------|--------|
| `_tmp_gt.py` | Leftover temp file from manual testing |
| `agent/` | Linux venv, non-portable, should not be distributed |
| `trends_20260402_1641.csv` | One-off output from `trend_scout.py` standalone run |
| Older `business_*.txt` files | Multiple generated inputs; keep only what's needed |
| Older `google_trends_*.json`, `trend_analysis_*.json`, etc. | Timestamped outputs accumulate; archive or delete |
