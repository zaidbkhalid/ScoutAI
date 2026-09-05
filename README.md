# Agent For Trend Scouting and Marketing

AI-powered marketing intelligence for small businesses. Scans what's trending on Google right now, scores each trend's actual freshness from historical data, and uses GPT-4o to generate specific ad creatives and marketing strategies tailored to your business — not generic trend lists.

## How It Works

1. **Fetch trends** — pulls keyword interest data and realtime trending searches from Google Trends
2. **Score freshness** — computes velocity and age for each trend across historical snapshots, so rising topics score higher than stale ones
3. **Parse your business** — GPT-4o extracts a structured profile from your free-text business description
4. **Match & strategize** — GPT-4o maps each trend to your business with specific product ideas, content ideas, captions, and timing
5. **Generate report** — produces a styled HTML report with ranked opportunities

## Requirements

- **Python 3.12+**
- **OpenAI API key** (GPT-4o access required)
- Internet connection (Google Trends + OpenAI API)

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

### 3. Set your OpenAI API key

Create a `.env` file in the project root:

```
OPENAI_API_KEY=sk-...your key here...
```

Or set it as an environment variable directly:

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

Open [http://localhost:5000](http://localhost:5000) in your browser.

Fill in the 4-step form with your business details, choose a country and trend keywords, then submit. The pipeline runs in the background with live progress updates. When it finishes, download your HTML report.

To enable Flask debug mode (development only):

```bash
# Linux / macOS
export FLASK_DEBUG=1

# Windows (PowerShell)
$env:FLASK_DEBUG = "1"
```

### Option B: Command line

```bash
python run.py my_business.txt
```

**Arguments:**

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `business_txt` | Yes | — | Path to a `.txt` file describing your business |
| `--geo` | No | `PK` | Country code: `PK`, `US`, `IN`, `GB`, `AE`, or empty for worldwide |
| `--keywords` | No | `AI,marketing,business,social media,ecommerce` | Comma-separated trend keywords (max 5) |
| `--skip-fetch` | No | off | Skip fetching trends and reuse existing JSON files |

**Examples:**

```bash
# Pakistan, default keywords
python run.py my_business.txt

# United States, custom keywords
python run.py my_business.txt --geo US --keywords "coffee,cafe,espresso,latte,cappuccino"

# Reuse previously fetched trends (no API calls to Google)
python run.py my_business.txt --skip-fetch
```

The CLI prints progress to the terminal and outputs:
- `trend_analysis_<timestamp>.json` — structured analysis data
- `trend_report_<timestamp>.html` — visual report (open in any browser)

### Option C: Run individual modules

Each step can be run standalone:

```bash
# 1. Fetch keyword trends
python google_trends.py --keywords "PSL,rain Karachi,Eid" --geo PK

# 2. Fetch realtime trending searches
python google_trending_now.py --geo PK

# 3. Score trend freshness across historical snapshots
python trend_scoring.py

# 4. Parse a business description into structured JSON
python parse_business.py my_business.txt

# 5. Analyze trends against the business (requires OPENAI_API_KEY)
python analyze_trends.py --keyword google_trends_*.json --realtime google_trending_now_*.json --scores trend_scores_*.json

# 6. Generate HTML report
python generate_report.py trend_analysis_*.json
```

## Testing

Run the trend scoring tests (no API key needed):

```bash
python test_trend_scoring.py
```

## Project Structure

```
├── app.py                      # Flask web server and API
├── run.py                      # CLI pipeline runner
├── google_trends.py            # Fetch keyword-based Google Trends data
├── google_trending_now.py      # Fetch realtime trending searches
├── trend_scoring.py            # Compute freshness/velocity scores
├── analyze_trends.py           # GPT-4o trend-to-business matching
├── parse_business.py           # GPT-4o business profile extraction
├── generate_report.py          # HTML report generator
├── test_trend_scoring.py       # Scoring module tests
├── requirements.txt            # Python dependencies
├── .env                        # API keys (you create this, never committed)
├── static/
│   └── index.html              # Web UI frontend
├── snapshots/                  # Historical trend data for freshness scoring
└── unused/
    └── trend_scout.py          # Disconnected multi-source scout (Reddit, Twitter, RSS)
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for GPT-4o |
| `FLASK_DEBUG` | No | `0` | Set to `1` to enable Flask debug mode |

## Notes

- Google Trends data is fetched via [pytrends](https://github.com/GeneralMills/pytrends), an unofficial wrapper. Google may rate-limit requests (HTTP 429). The code includes exponential backoff retry logic, but frequent runs may still hit limits.
- Trend keywords are limited to **5 per request** (Google's limit).
- The freshness scoring (`trend_scoring.py`) becomes more accurate over time as more snapshot files accumulate in `snapshots/`. A single run provides limited historical signal; running the pipeline daily builds up meaningful velocity data.
- The `unused/trend_scout.py` module contains RSS, Reddit, and Twitter integrations that are not wired into the main pipeline. The RSS portion works without API keys.
