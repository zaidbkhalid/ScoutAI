# ScoutAI

AI-powered marketing intelligence for small businesses. Scans what's genuinely trending right now (not stale, not generic), scores each trend's actual freshness from real data, and turns the ones that fit your business into concrete campaign ideas — plus a separate read on your competitors and on whether AI answer engines currently recommend you.

## How It Works

**1. Trend Analysis** (staged, so you're never handed a wall of output at once)
- **Discover trends — no input required.** Pick a region and hit "Fetch Trends". Nothing about your business is needed at this point. Pulls live trending searches from Google (public Daily Search Trends RSS, with approximate search volume and the news story behind each spike), trending videos from YouTube, articles from a curated set of RSS feeds (regional + topical, capped to the last few days), and hashtags from TikTok Creative Center. Every source normalizes to one shared schema (`snapshot_schema.py`) and gets scored for real freshness/velocity (`trend_scoring.py`), filtered for brand safety and synthetic placeholders. You get the top 3 trends, filterable by source (All / Google / YouTube / TikTok / News), preferring anything from the last 24 hours.
- **Match to your business — optional, second.** Enter your business details and GPT-4o judges which of the *already-fetched* trends have a genuine connection to you and why (`trend_analyzer.py`). Nothing is re-fetched; this is purely the cross-reference step.
- **Campaign ideas** — pick the trends worth acting on, and GPT-4o turns each into a campaign concept: core idea, audience angle, timing, and concrete execution directions (`campaign_ideas.py`).
- **Ad copy** — pick a campaign and the formats you want, and GPT-4o writes publishable copy for each (`ad_copy.py`). 20 formats across social posts, short-video scripts, email/WhatsApp/SMS, paid ads, and longer form — see `ad_formats.py`, which is the single source of truth for the catalogue and carries each platform's real constraints (Google's 30-character headline cap, the shape of a TikTok script, where an email subject line goes). One model call covers every requested format so they stay consistent with each other.

**2. Competitor Check** (separate tab, doesn't require a trend analysis first)
- **Check competitors** — give it a couple of competitor names + URLs and it reads their own homepages for positioning, patterns across them, and where you could differentiate (`competitor_research.py` → `competitor_synthesis.py`).
- **Validate my idea** — same competitor data, but stress-tested against something specific you're considering (`idea_validation.py`).

**3. AI Visibility Check** (optional, folded into the trend analysis flow if you add a website)
- Audits your site's structured data (`structured_data_audit.py`) and asks Perplexity a handful of real "best X in \<city\>" questions to see whether you get cited (`ai_visibility_check.py`), then synthesizes both into gaps and fixes (`ai_legibility_synthesis.py`).

## Demo mode (no API key needed)

Set `DEMO_MODE=1` in `.env` to run the entire product — trend analysis, campaign ideas, competitor research, and the AI legibility check — with **zero OpenAI/Perplexity spend**. This only works for 3 of the 6 built-in example businesses on the landing page (Velvet Crumbs, FirePit BBQ Co., Glowly Skincare); the other three (Rep Range Studio, Khaata, Kite & Kora) have no canned data and always use the real API. Every script that would normally call OpenAI or Perplexity instead loads a hand-written canned response from `mock_data/<preset>/` for those 3 businesses specifically.

**Note:** `DEMO_MODE` is read from the environment of the *already-running* process. `app.py` does not reload `.env`, and child scripts inherit its environment before `load_dotenv()` runs (which does not override an already-set variable). Editing `.env` therefore has no effect until you **restart the server**.

Free trend sources (Google, YouTube, RSS, TikTok) are untouched by demo mode and always fetch real, live data — only the GPT-4o/Perplexity steps are mocked.

This is meant to be reversed once a real API key is funded: set `DEMO_MODE=0` (or remove the line) and restart the server to go back to live API calls everywhere, no other changes needed. See `demo_mode.py` for how it works.

## Requirements

- **Python 3.12+**
- **OpenAI API key** (GPT-4o access required, unless using `DEMO_MODE`)
- Optional: `PERPLEXITY_API_KEY` (only needed for the AI Visibility Check step) and `YOUTUBE_API_KEY` (only needed for the YouTube trending step — the rest of the pipeline still runs without it)
- Internet connection

## Setup

### 1. Create a virtual environment

```bash
python -m venv venv

# Activate it:
# Linux / macOS
source venv/bin/activate

# Windows (PowerShell)
venv\Scripts\Activate.ps1

# Windows (cmd)
venv\Scripts\activate.bat
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your API key(s)

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...your key here...

# Optional
PERPLEXITY_API_KEY=pplx-...
YOUTUBE_API_KEY=...
DEMO_MODE=0
```

Or set `OPENAI_API_KEY` as an environment variable directly:

```bash
# Linux / macOS
export OPENAI_API_KEY="sk-..."

# Windows (PowerShell)
$env:OPENAI_API_KEY = "sk-..."
```

## Running

### Option A: Web UI (recommended)

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser. The landing page explains what ScoutAI does and lets you either fill in your own business or click one of six demo businesses to try it instantly. From there:

- **Trend Analysis** tab: pick a region and fetch trends immediately — no business details needed. You get the top 3, filterable by source. Then optionally add your business details to cross-reference those same trends against it (with a "why this fits" reasoning and a relevance score), and select the ones you want to generate campaign ideas from. Add your website on the last form step to also get the AI Visibility Check.
- **Competitor Check** tab: independent of the above — give it competitor names/URLs and either get a positioning read or validate a specific idea against them.

Everything runs in the background with live progress updates; each submission gets its own isolated working directory (`jobs/<job_id>/`) so concurrent submissions never cross-contaminate.

To enable Flask debug mode (development only):

```bash
# Linux / macOS
export FLASK_DEBUG=1

# Windows (PowerShell)
$env:FLASK_DEBUG = "1"
```

### Option B: Command line (single-shot, legacy)

The original one-shot pipeline still works standalone — fetch, score, and analyze in one call, producing a single JSON + HTML report, rather than the staged flow the web UI uses:

```bash
python run.py my_business.txt
```

**Arguments:**

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `business_txt` | Yes | — | Path to a `.txt` file describing your business |
| `--geo` | No | `PK` | Country code: `PK`, `US`, `IN`, `GB`, `AE`, or empty for worldwide |
| `--skip-fetch` | No | off | Skip fetching trends and reuse existing JSON files |

```bash
python run.py my_business.txt --geo US
python run.py my_business.txt --skip-fetch   # reuse previously fetched trends
```

Outputs `trend_analysis_<timestamp>.json` and `trend_report_<timestamp>.html` (open the HTML in any browser).

### Option C: Run individual modules

Each step can be run standalone:

```bash
# 1. Fetch live trending searches (Google's public Daily Search Trends RSS)
python google_trending_now.py --geo PK

# 2. Fetch YouTube trending videos (requires YOUTUBE_API_KEY)
python youtube_trending.py --geo PK

# 3. Fetch RSS articles -- topical feeds + regional feeds for --geo
python rss_trends.py --geo PK --max-age-days 3

# 4. Fetch TikTok Creative Center hashtags (mock fallback if live extraction fails)
python tiktok_creative_center.py --geo PK

# 5. Score trend freshness across snapshots (brand-safety + mock-data filtered by default)
python trend_scoring.py

# 6. Parse a business description into structured JSON
python parse_business.py my_business.txt

# 7. Stage 1: top trends + trends matched to the business (requires OPENAI_API_KEY)
python trend_analyzer.py --business business_profile.json

# 8. Stage 2: campaign ideas from trends you select (selected_trends.json -> {"trends": [...]})
python campaign_ideas.py

# 9. Stage 3: ad copy for one campaign in the formats you name
#    (selected_campaign.json -> {"campaign": {...}, "formats": [...]})
python ad_copy.py --formats instagram_post,email_campaign,tiktok_script
```

`google_trends.py` (keyword-based Google Trends interest-over-time) is a separate standalone tool, not part of the automatic pipeline: `python google_trends.py --keywords "PSL,rain Karachi,Eid" --geo PK`

## Testing

Run the trend scoring tests (no API key needed):

```bash
python test_trend_scoring.py
```

## Project Structure

```
├── app.py                        # Flask web server and API (the staged, 3-tab flow)
├── run.py                        # CLI: original single-shot pipeline
├── snapshot_schema.py            # Shared normalization schema for all trend sources
├── brand_safety.py               # Filters death/violence/crime/disaster out of ranked trends
├── demo_mode.py                  # DEMO_MODE scaffolding (see "Demo mode" above)
│
├── google_trends.py               # Standalone: keyword-based Google Trends data (not in the pipeline)
├── google_trending_now.py         # Fetch live trending searches (public Trends RSS)
├── youtube_trending.py            # Fetch YouTube trending videos
├── rss_trends.py                  # Fetch topical + regional RSS articles
├── tiktok_creative_center.py      # Fetch TikTok trending hashtags (mock fallback)
├── trend_scoring.py                # Freshness/velocity scoring across snapshots
│
├── parse_business.py              # GPT-4o business profile extraction
├── trend_analyzer.py              # Stage 1: top trends + trends matched to the business
├── campaign_ideas.py              # Stage 2: campaign concepts from selected trends
├── ad_copy.py                     # Stage 3: publishable copy per platform
├── ad_formats.py                  # The ad-copy format catalogue (20 formats)
├── analyze_trends.py              # Legacy one-shot trend-to-business matching (used by run.py)
├── generate_report.py             # Legacy HTML report generator (used by run.py)
│
├── competitor_research.py         # Fetch competitor homepages
├── competitor_synthesis.py        # Competitor positioning + differentiation read
├── idea_validation.py             # Validate a specific idea against competitor data
├── structured_data_audit.py       # AI Visibility Check: site structured-data audit
├── ai_visibility_check.py         # AI Visibility Check: real Perplexity queries
├── ai_legibility_synthesis.py     # AI Visibility Check: synthesizes the above two
│
├── image_generation.py            # Standalone prototype: campaign visuals via OpenAI Images API
│                                   #   (not wired into app.py/the web UI yet)
│
├── test_trend_scoring.py          # Scoring module tests
├── requirements.txt                # Python dependencies
├── .env                            # API keys (you create this, never committed)
│
├── static/
│   └── index.html                 # Web UI frontend
├── mock_data/                      # DEMO_MODE canned responses for the 3 built-in presets
└── jobs/                           # Per-submission working directories (gitignored)
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes* | — | OpenAI API key for GPT-4o. *Not required if `DEMO_MODE=1` and you're only using the 3 built-in demo businesses. |
| `PERPLEXITY_API_KEY` | No | — | Needed for the AI Visibility Check step (`ai_visibility_check.py`). If unset, that step logs a clear message and the rest of the pipeline still runs. |
| `YOUTUBE_API_KEY` | No | — | Free key from Google Cloud Console (YouTube Data API v3). If unset, that step logs an error and is skipped — the rest of the pipeline still runs. |
| `DEMO_MODE` | No | `0` | See "Demo mode" above. Requires restarting the server after changing it. |
| `FLASK_DEBUG` | No | `0` | Set to `1` to enable Flask debug mode |

## Notes

- `google_trending_now.py` reads Google's public Daily Search Trends RSS feed (`https://trends.google.com/trending/rss?geo=<CC>`) — no API key, no quota. It carries an approximate search volume per term plus the news story behind the spike, both used downstream. It previously used [pytrends](https://github.com/GeneralMills/pytrends), which now fails with HTTP 404 against that endpoint; `google_trends.py` (keyword-based, standalone) still uses pytrends and may still rate-limit.
- Regional news feeds are selected by `--geo` (PK, GB, US, IN, AE). There is no working UAE feed — Gulf News, Khaleej Times, The National, Zawya, Arabian Business and Emirates247 all 404, hard-block, or return nothing — so AE falls back to international outlets.
- Trends about death, violence, crime and disaster are filtered out of the ranked scores by default (`brand_safety.py`), since the UI invites you to turn that list into marketing. Snapshots keep the unfiltered record; pass `--include-unsafe` to `trend_scoring.py` to see everything.
- Clearly-labeled synthetic placeholder entities (what `tiktok_creative_center.py` emits when live extraction is unavailable) are likewise excluded from ranked scores by default, so they can never surface as if they were real trends. Pass `--include-mock` to keep them.
- The relevance pool sent to GPT-4o is sampled across categories rather than taken straight off the top of the freshness ranking — daily news and search feeds publish far more often than topical ones, and a straight top-N leaves a bakery with nothing but football and headlines to match against.
- The automatic pipeline needs no keywords: every source it fetches is inherently "what's popular right now," and RSS articles are dropped once older than `--max-age-days` (default 3). `google_trends.py`'s keyword-based endpoint is available separately if you want data for specific keywords, capped to 5 per request (Google's limit).
- The freshness scoring (`trend_scoring.py`) becomes more accurate over time as more snapshot files accumulate. A single run has limited historical signal to compute velocity from; running the pipeline repeatedly builds it up.
