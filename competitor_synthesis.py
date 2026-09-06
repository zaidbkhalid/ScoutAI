"""
competitor_synthesis.py
------------------------
Standalone demo: sends the business profile + fetched competitor homepage
text (from competitor_research.py) to GPT-4o and asks for observations and
informed hypotheses about competitor positioning and differentiation
angles -- explicitly not predictions or guarantees. Not wired into the
main trend pipeline.

Reads the latest competitor_snapshots_<timestamp>.json by default.

Output: competitor_analysis_<timestamp>.json, plus a readable printout.

Usage:
    python competitor_synthesis.py
    python competitor_synthesis.py --snapshot competitor_snapshots_20260906_120000.json
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OPENAI_API = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o"

BUSINESS_PROFILE_PATH = "business_profile.json"

SYSTEM_PROMPT = """You are a marketing analyst helping a small business owner understand \
their competitive landscape.

Based on what these competitors are visibly doing on their own websites, describe patterns \
in how they position themselves, what they seem to emphasize, and any gaps or angles this \
business could differentiate on.

Do not claim any strategy will succeed or fail. Do not predict outcomes. Present everything \
as observations and informed hypotheses -- context for the business owner's own judgment, \
not a verdict. If the competitor text is too thin to say something meaningful, say so plainly \
instead of inventing detail.

Return ONLY valid JSON. No markdown fences."""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def build_user_prompt(business, competitors):
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## Competitor Homepage Text (visible text extracted from each site)
{json.dumps(competitors, indent=2)}

Analyze the competitors above relative to this business.

Return this exact JSON structure:
{{
  "competitor_summaries": [
    {{
      "name": "competitor name",
      "positioning_summary": "what this competitor's homepage suggests about how they position themselves",
      "notable_emphasis": ["specific things they visibly emphasize, e.g. price, speed, premium ingredients"]
    }}
  ],
  "cross_competitor_patterns": [
    "a pattern most or all of the competitors visibly share"
  ],
  "differentiation_angles": [
    {{
      "angle": "a possible way this business could differentiate",
      "reasoning": "why this angle looks open, based on what competitors do or don't emphasize"
    }}
  ],
  "caveat": "a short reminder that this is based only on homepage text and informed judgment, not a verdict or guarantee"
}}"""


def analyze(business, competitors):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set. Add it to your .env file.")

    resp = requests.post(
        OPENAI_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": MODEL,
            "temperature": 0.4,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(business, competitors)},
            ],
        },
        timeout=90,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


def print_readable(result):
    print("\n" + "=" * 60)
    print("  COMPETITOR ANALYSIS")
    print("=" * 60)

    for c in result.get("competitor_summaries", []):
        print(f"\n  {c.get('name', '(unnamed)')}")
        print(f"    {c.get('positioning_summary', '')}")
        emphasis = c.get("notable_emphasis", [])
        if emphasis:
            print(f"    Emphasizes: {', '.join(emphasis)}")

    patterns = result.get("cross_competitor_patterns", [])
    if patterns:
        print(f"\n  Cross-competitor patterns:")
        for p in patterns:
            print(f"    - {p}")

    angles = result.get("differentiation_angles", [])
    if angles:
        print(f"\n  Possible differentiation angles:")
        for a in angles:
            print(f"    - {a.get('angle', '')}")
            print(f"      Reasoning: {a.get('reasoning', '')}")

    caveat = result.get("caveat", "")
    if caveat:
        print(f"\n  Caveat: {caveat}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=None,
                         help="Path to competitor_snapshots_*.json "
                              "(default: latest found)")
    parser.add_argument("--business", default=BUSINESS_PROFILE_PATH)
    args = parser.parse_args()

    print("Competitor Synthesis (standalone demo)")
    print("-" * 40)

    if not Path(args.business).exists():
        print(f"  {args.business} not found.")
        print(f"  Run: python parse_business.py my_business.txt")
        sys.exit(1)
    business = load_json(args.business)

    snapshot_path = args.snapshot or find_latest("competitor_snapshots_*.json")
    if not snapshot_path or not Path(snapshot_path).exists():
        print("  No competitor_snapshots_*.json found.")
        print("  Run: python competitor_research.py")
        sys.exit(1)

    print(f"  Business : {args.business}")
    print(f"  Snapshot : {snapshot_path}\n")

    snapshot = load_json(snapshot_path)
    all_competitors = snapshot.get("competitors", [])
    successful = [c for c in all_competitors if c.get("success") and c.get("text")]

    print(f"  Competitors in snapshot : {len(all_competitors)}")
    print(f"  With usable text        : {len(successful)}")

    if not successful:
        print("\n  No competitor pages were successfully fetched in this "
              "snapshot, so there's nothing to synthesize.")
        print("  Fill in real URLs in competitor_urls.json, run "
              "competitor_research.py again, then retry this script.")
        sys.exit(0)

    competitors_for_prompt = [
        {"name": c["name"], "url": c["url"], "text": c["text"]}
        for c in successful
    ]

    print(f"\n  Sending {len(competitors_for_prompt)} competitor(s) to "
          f"GPT-4o...\n")
    try:
        result = analyze(business, competitors_for_prompt)
    except requests.exceptions.HTTPError as e:
        detail = ""
        if e.response is not None:
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = e.response.text[:300]
        print(f"  ERROR: OpenAI API request failed: {e}")
        if detail:
            print(f"  Detail: {detail}")
        sys.exit(1)
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    result["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "business_file": str(Path(args.business).name),
        "snapshot_file": str(Path(snapshot_path).name),
        "competitor_count": len(competitors_for_prompt),
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = f"competitor_analysis_{ts}.json"
    Path(out_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Analysis saved -> {out_path}")

    print_readable(result)


if __name__ == "__main__":
    main()
