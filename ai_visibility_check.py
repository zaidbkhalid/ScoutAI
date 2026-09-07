"""
ai_visibility_check.py
------------------------
Standalone demo: asks Perplexity (an AI answer engine) a handful of
natural customer-style questions and checks whether this business shows
up in the answer at all, and what gets cited instead when it doesn't.
Not wired into the main trend pipeline.

Requires PERPLEXITY_API_KEY in .env. Uses "sonar", Perplexity's cheapest
Sonar model tier -- plenty for this kind of short factual query.

Query construction needs a real place name, not just business_profile.json's
"geographic_focus" field (which is only "local"/"national"/"global" -- a
category, not a location). Location resolution order:
  1. business_profile.json's "location" field (added by parse_business.py)
  2. A rough guess extracted from target_audience.demographics text
  3. geographic_focus itself, labeled as a coarse fallback
  4. No location at all -- queries are built without a place name

Per-query failures (timeout, HTTP error, malformed response) are logged as
warnings and do not stop the rest of the run.

Output: ai_visibility_<timestamp>.json

Usage:
    python ai_visibility_check.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from demo_mode import is_demo_mode, resolve_preset_key, load_mock

load_dotenv()

PERPLEXITY_API = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar"  # cheapest Sonar tier

BUSINESS_PROFILE_PATH = "business_profile.json"
REQUEST_TIMEOUT = 60


def load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def resolve_location(business):
    """Returns (location_str_or_empty, how_it_was_derived)."""
    location = business.get("location")
    if location and str(location).strip():
        return str(location).strip(), "business_profile.json location field"

    demographics = business.get("target_audience", {}).get("demographics", "")
    match = re.search(
        r"\bin ([A-Z][a-zA-Z]+(?:,?\s+[A-Z][a-zA-Z]+)?)", demographics)
    if match:
        return match.group(1), "guessed from target_audience.demographics"

    geo = business.get("geographic_focus", "")
    if geo and geo not in ("local", "national", "global"):
        return geo, "geographic_focus field"
    if geo:
        return "", f"geographic_focus is only '{geo}' -- not a usable place name"

    return "", "no location information available"


def build_queries(business, location):
    business_name = business.get("business_name", "") or "this business"
    industry = business.get("industry", "") or "business"
    sub_industry = business.get("sub_industry", "") or industry
    products = business.get("products_or_services", [])
    top_product = products[0] if products else sub_industry

    loc_suffix = f" in {location}" if location else ""
    loc_near = f" near {location}" if location else ""

    return [
        f"best {sub_industry}{loc_suffix}",
        f"where can I get {top_product}{loc_suffix}",
        f"{business_name} reviews",
        f"top-rated {industry}{loc_near}",
        f"recommend a good {sub_industry}{loc_suffix}",
    ]


def check_mention(text, business_name):
    """Returns (exact_match, partial_match) booleans."""
    if not business_name:
        return False, False
    text_l = text.lower()
    name_l = business_name.lower().strip()
    exact = name_l in text_l

    words = re.findall(r"[a-z0-9]+", name_l)
    first_word = words[0] if words else ""
    text_words = set(re.findall(r"[a-z0-9]+", text_l))
    partial = bool(first_word) and first_word in text_words

    return exact, partial


def query_perplexity(query, api_key):
    """Send one query to Perplexity. Returns a result dict; never raises."""
    try:
        resp = requests.post(
            PERPLEXITY_API,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": query}],
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        citations = data.get("citations", []) or []
        search_results = data.get("search_results", []) or []
        return {
            "query": query, "success": True,
            "response_text": content,
            "citations": citations,
            "search_results": search_results,
        }
    except requests.exceptions.Timeout:
        print(f"  WARNING: Timeout querying Perplexity for: {query!r}")
        error = "timeout"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        detail = ""
        if e.response is not None:
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = e.response.text[:200]
        print(f"  WARNING: HTTP {status} for {query!r}"
              f"{' -- ' + detail if detail else ''}")
        error = f"http error {status}: {detail}"
    except requests.exceptions.RequestException as e:
        print(f"  WARNING: Request failed for {query!r}: {e}")
        error = f"request error: {e}"
    except (KeyError, IndexError, ValueError) as e:
        print(f"  WARNING: Unexpected response shape for {query!r}: {e}")
        error = f"unexpected response: {e}"

    return {"query": query, "success": False, "error": error}


def main():
    print("AI Visibility Check (standalone demo)")
    print("-" * 40)

    if not Path(BUSINESS_PROFILE_PATH).exists():
        print(f"  {BUSINESS_PROFILE_PATH} not found.")
        print(f"  Run: python parse_business.py my_business.txt")
        sys.exit(1)
    business = load_json(BUSINESS_PROFILE_PATH)

    # -- DEMO_MODE: skip the real Perplexity requirement entirely for the 3
    # built-in synthetic businesses (no key needed at all), using canned
    # query results instead. See demo_mode.py. Falls through to the real
    # flow below (including the API key check) for anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business.get("business_name", ""))
        mock = load_mock(preset_key, "ai_visibility.json")
        if mock:
            print(f"  [DEMO_MODE] Using canned visibility check for "
                  f"'{business.get('business_name')}' -- no API call made, "
                  f"no PERPLEXITY_API_KEY needed.\n")
            out = dict(mock)
            out["_meta"] = dict(out.get("_meta", {}))
            out["_meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            out_path = f"ai_visibility_{ts}.json"
            Path(out_path).write_text(
                json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
            meta = out["_meta"]
            print(f"  Queries succeeded : {meta.get('succeeded')}/{meta.get('total_queries')}")
            print(f"  Business mentioned: {meta.get('mentioned_count')}/{meta.get('succeeded') or 1}")
            print(f"  Saved -> {out_path}")
            print(f"\n  Next: python ai_legibility_synthesis.py")
            return
    # -- end DEMO_MODE --

    api_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not api_key:
        print("  ERROR: PERPLEXITY_API_KEY not set.")
        print("  Sign up at https://www.perplexity.ai/, open the API")
        print("  settings (a Perplexity account with API access / billing")
        print("  enabled -- separate from a Pro subscription), generate")
        print("  a key, then add to .env:")
        print("    PERPLEXITY_API_KEY=pplx-...")
        sys.exit(1)

    business_name = business.get("business_name", "")
    location, location_source = resolve_location(business)
    print(f"  Business : {business_name or '(unnamed)'}")
    print(f"  Location : {location or '(none)'} [{location_source}]\n")

    queries = build_queries(business, location)
    print(f"  Sending {len(queries)} queries to Perplexity ({MODEL})...\n")

    results = []
    for q in queries:
        print(f"  -> {q}")
        result = query_perplexity(q, api_key)
        if result["success"]:
            exact, partial = check_mention(
                result["response_text"], business_name)
            result["mentioned_exact"] = exact
            result["mentioned_partial"] = partial
            mention_str = "MENTIONED" if exact else (
                "partial match" if partial else "not mentioned")
            print(f"     {mention_str} "
                  f"({len(result['citations'])} citation(s))")
        results.append(result)

    succeeded = sum(1 for r in results if r["success"])
    mentioned = sum(1 for r in results if r.get("mentioned_exact"))

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "business_file": BUSINESS_PROFILE_PATH,
            "business_name": business_name,
            "location_used": location,
            "location_source": location_source,
            "model": MODEL,
            "total_queries": len(queries),
            "succeeded": succeeded,
            "failed": len(queries) - succeeded,
            "mentioned_count": mentioned,
        },
        "results": results,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = f"ai_visibility_{ts}.json"
    Path(out_path).write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  Queries succeeded : {succeeded}/{len(queries)}")
    print(f"  Business mentioned: {mentioned}/{succeeded or 1}")
    print(f"  Saved -> {out_path}")
    if succeeded:
        print(f"\n  Next: python ai_legibility_synthesis.py")


if __name__ == "__main__":
    main()
