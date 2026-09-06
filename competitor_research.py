"""
competitor_research.py
-----------------------
Standalone demo: fetches competitor homepages and extracts visible text
for later synthesis. Not wired into the main trend pipeline.

Source of truth for *which* competitors to research is competitor_urls.json
(name -> URL). business_profile.json's "competitors_or_market" field is a
free-text description (not a structured list), so it's only used here for
context in the printed summary, not as the driver list.

Competitors with no URL filled in yet are skipped with a note.
Per-competitor fetch failures (timeout, 403, DNS, etc.) are logged as
warnings and do not stop the rest of the run.

Output: competitor_snapshots_<timestamp>.json

Usage:
    python competitor_research.py
"""

import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BUSINESS_PROFILE_PATH = "business_profile.json"
COMPETITOR_URLS_PATH = "competitor_urls.json"

REQUEST_TIMEOUT = 15  # seconds
MAX_TEXT_CHARS = 5000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  WARNING: {path} is not valid JSON ({e}). Ignoring it.")
        return None


def extract_visible_text(raw_html):
    """Strip scripts/styles/tags, unescape entities, collapse whitespace."""
    no_script_style = SCRIPT_STYLE_RE.sub(" ", raw_html)
    no_tags = TAG_RE.sub(" ", no_script_style)
    unescaped = html.unescape(no_tags)
    collapsed = WHITESPACE_RE.sub(" ", unescaped).strip()
    return collapsed[:MAX_TEXT_CHARS]


def fetch_competitor(name, url):
    """Fetch one competitor homepage. Returns a result dict; never raises."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        text = extract_visible_text(resp.text)
        if not text:
            print(f"  WARNING: {name} ({url}) returned no extractable text.")
            return {
                "name": name, "url": url, "success": False,
                "error": "no extractable text", "fetched_at": fetched_at,
            }
        print(f"  OK: {name} -> {len(text)} chars extracted")
        return {
            "name": name, "url": url, "success": True,
            "text": text, "fetched_at": fetched_at,
        }
    except requests.exceptions.Timeout:
        print(f"  WARNING: Timeout fetching {name} ({url}).")
        error = "timeout"
    except requests.exceptions.ConnectionError as e:
        print(f"  WARNING: Connection failed for {name} ({url}): {e}")
        error = f"connection error: {e}"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"  WARNING: {name} ({url}) returned HTTP {status}.")
        error = f"http error: {status}"
    except requests.exceptions.RequestException as e:
        print(f"  WARNING: Request failed for {name} ({url}): {e}")
        error = f"request error: {e}"
    except Exception as e:
        print(f"  WARNING: Unexpected error fetching {name} ({url}): {e}")
        error = f"unexpected error: {e}"

    return {
        "name": name, "url": url, "success": False,
        "error": error, "fetched_at": fetched_at,
    }


def main():
    print("Competitor Research (standalone demo)")
    print("-" * 40)

    business = load_json(BUSINESS_PROFILE_PATH)
    if business:
        market = business.get("competitors_or_market", "")
        if market:
            print(f"  Business's stated competitors/market: {market}")
    else:
        print(f"  NOTE: {BUSINESS_PROFILE_PATH} not found. "
              f"Continuing with competitor_urls.json only.")

    competitor_urls = load_json(COMPETITOR_URLS_PATH)
    if competitor_urls is None:
        print(f"\n  ERROR: {COMPETITOR_URLS_PATH} not found or invalid.")
        print(f"  Create it as a JSON object mapping competitor name -> URL.")
        sys.exit(1)

    if not isinstance(competitor_urls, dict):
        print(f"\n  ERROR: {COMPETITOR_URLS_PATH} must be a JSON object "
              f"of {{\"name\": \"url\"}} pairs.")
        sys.exit(1)

    print(f"  Loaded {len(competitor_urls)} competitor entries "
          f"from {COMPETITOR_URLS_PATH}\n")

    results = []
    skipped = 0
    for name, url in competitor_urls.items():
        if not url or not str(url).strip():
            print(f"  Skipped: {name} (no URL filled in yet)")
            skipped += 1
            continue
        results.append(fetch_competitor(name, str(url).strip()))

    fetched_ok = sum(1 for r in results if r["success"])

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "business_file": BUSINESS_PROFILE_PATH if business else None,
            "total_configured": len(competitor_urls),
            "skipped_no_url": skipped,
            "attempted": len(results),
            "succeeded": fetched_ok,
            "failed": len(results) - fetched_ok,
        },
        "competitors": results,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = f"competitor_snapshots_{ts}.json"
    Path(out_path).write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Configured : {len(competitor_urls)}")
    print(f"  Skipped    : {skipped} (no URL yet)")
    print(f"  Attempted  : {len(results)}")
    print(f"  Succeeded  : {fetched_ok}")
    print(f"  Failed     : {len(results) - fetched_ok}")
    print(f"\n  Saved -> {out_path}")
    if fetched_ok:
        print(f"\n  Next: python competitor_synthesis.py")
    else:
        print(f"\n  No competitor pages were fetched successfully. "
              f"Fill in real URLs in {COMPETITOR_URLS_PATH} and re-run.")


if __name__ == "__main__":
    main()
