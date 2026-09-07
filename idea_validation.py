"""
idea_validation.py
--------------------
Standalone demo: takes a specific idea/plan the business owner is
considering, plus fetched competitor homepage text (from
competitor_research.py), and asks GPT-4o for a grounded read on whether
the idea looks promising -- explicitly framed as informed reasoning, not
a prediction or guarantee. Not wired into the main trend pipeline.

This is the "would this work?" sibling to competitor_synthesis.py's
"what are they doing?" -- same competitor data, different question.

Reads business_idea.json ({"idea": "..."}) and the latest
competitor_snapshots_<timestamp>.json.

Output: idea_validation_<timestamp>.json, plus a readable printout.

Usage:
    python idea_validation.py
    python idea_validation.py --snapshot competitor_snapshots_20260906_120000.json
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

from demo_mode import is_demo_mode, resolve_preset_key, load_mock

load_dotenv()

OPENAI_API = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o"

BUSINESS_PROFILE_PATH = "business_profile.json"
BUSINESS_IDEA_PATH = "business_idea.json"

SYSTEM_PROMPT = """You are a business strategy consultant helping a small business owner think \
through a specific idea they're considering -- a new product line, a change in operating \
model, a new offering, etc.

You'll be given the business's own profile, the idea itself, and visible information about \
their competitors' homepages. Give an honest, grounded read on whether the idea looks \
promising, using specific parallels to the competitor data where relevant: e.g. a competitor \
already doing something similar (a signal it's a proven-enough model to compete in) or no \
competitor doing it at all (which could mean open opportunity, or could mean unproven demand -- \
say which reading seems more likely and why, if you can).

Do not predict success or failure. Do not guarantee any outcome. Present this as informed \
reasoning to help the business owner think it through -- not a verdict. If the available data \
is too thin to say something meaningful about a section, say so plainly instead of inventing \
detail.

Return ONLY valid JSON. No markdown fences."""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def build_user_prompt(business, idea, competitors):
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## The Idea Being Considered
{idea}

## Competitor Homepage Text (visible text extracted from each site)
{json.dumps(competitors, indent=2)}

Assess the idea above relative to this business and its competitors.

Return this exact JSON structure:
{{
  "idea_recap": "a one-sentence restatement of the idea in your own words, to confirm understanding",
  "overall_read": "promising | worth testing | mixed | proceed with caution | hard to assess from available data",
  "supporting_signals": [
    {{
      "signal": "a specific reason this idea could work",
      "reasoning": "why this signal is meaningful, grounded in the business or competitor data"
    }}
  ],
  "risk_signals": [
    {{
      "signal": "a specific reason for caution",
      "reasoning": "why this risk is worth taking seriously, grounded in the business or competitor data"
    }}
  ],
  "competitor_parallels": [
    {{
      "competitor": "competitor name",
      "observation": "what this competitor's own site suggests is relevant to the idea -- doing something similar, notably not doing it, or a related signal"
    }}
  ],
  "suggested_next_step": "one concrete, low-risk way to test this idea before fully committing to it",
  "caveat": "a short reminder that this is informed reasoning from limited data, not a guarantee of success or failure"
}}"""


def analyze(business, idea, competitors):
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
                {"role": "user", "content": build_user_prompt(business, idea, competitors)},
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
    print("  IDEA VALIDATION")
    print("=" * 60)

    print(f"\n  Idea: {result.get('idea_recap', '')}")
    print(f"  Overall read: {result.get('overall_read', '')}")

    supporting = result.get("supporting_signals", [])
    if supporting:
        print(f"\n  Supporting signals:")
        for s in supporting:
            print(f"    + {s.get('signal', '')}")
            print(f"      {s.get('reasoning', '')}")

    risks = result.get("risk_signals", [])
    if risks:
        print(f"\n  Risk signals:")
        for r in risks:
            print(f"    - {r.get('signal', '')}")
            print(f"      {r.get('reasoning', '')}")

    parallels = result.get("competitor_parallels", [])
    if parallels:
        print(f"\n  Competitor parallels:")
        for p in parallels:
            print(f"    - {p.get('competitor', '')}: {p.get('observation', '')}")

    next_step = result.get("suggested_next_step", "")
    if next_step:
        print(f"\n  Suggested next step: {next_step}")

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
    parser.add_argument("--idea", default=None,
                         help="The idea to validate (default: read from "
                              "business_idea.json)")
    args = parser.parse_args()

    print("Idea Validation (standalone demo)")
    print("-" * 40)

    if not Path(args.business).exists():
        print(f"  {args.business} not found.")
        print(f"  Run: python parse_business.py my_business.txt")
        sys.exit(1)
    business = load_json(args.business)

    idea = args.idea
    if not idea:
        if not Path(BUSINESS_IDEA_PATH).exists():
            print(f"  No idea provided. Pass --idea or create {BUSINESS_IDEA_PATH} "
                  f"as {{\"idea\": \"...\"}}.")
            sys.exit(1)
        idea = load_json(BUSINESS_IDEA_PATH).get("idea", "").strip()
        if not idea:
            print(f"  {BUSINESS_IDEA_PATH} exists but has no idea text.")
            sys.exit(1)

    # -- DEMO_MODE: skip the real fetch+API requirement entirely for the 3
    # built-in synthetic businesses, using a hand-written canned validation
    # instead (a fixed example idea, regardless of what was actually typed
    # -- same limitation as every other DEMO_MODE intercept in this
    # project). See demo_mode.py. Falls through to the real flow below for
    # anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business.get("business_name", ""))
        mock = load_mock(preset_key, "idea_validation.json")
        if mock:
            print(f"  [DEMO_MODE] Using a canned example idea validation for "
                  f"'{business.get('business_name')}' -- no fetch, no API "
                  f"call made. (Shows a fixed example idea, not what you "
                  f"typed.)\n")
            result = dict(mock)
            result["_meta"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "business_file": str(Path(args.business).name),
                "snapshot_file": "demo-mode",
                "_demo_mode": True,
            }
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            out_path = f"idea_validation_{ts}.json"
            Path(out_path).write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  Validation saved -> {out_path}")
            print_readable(result)
            return
    # -- end DEMO_MODE --

    snapshot_path = args.snapshot or find_latest("competitor_snapshots_*.json")
    if not snapshot_path or not Path(snapshot_path).exists():
        print("  No competitor_snapshots_*.json found.")
        print("  Run: python competitor_research.py")
        sys.exit(1)

    print(f"  Business : {args.business}")
    print(f"  Idea     : {idea}")
    print(f"  Snapshot : {snapshot_path}\n")

    snapshot = load_json(snapshot_path)
    all_competitors = snapshot.get("competitors", [])
    successful = [c for c in all_competitors if c.get("success") and c.get("text")]

    print(f"  Competitors in snapshot : {len(all_competitors)}")
    print(f"  With usable text        : {len(successful)}")

    competitors_for_prompt = [
        {"name": c["name"], "url": c["url"], "text": c["text"]}
        for c in successful
    ]

    print(f"\n  Sending idea + {len(competitors_for_prompt)} competitor(s) to "
          f"GPT-4o...\n")
    try:
        result = analyze(business, idea, competitors_for_prompt)
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
    out_path = f"idea_validation_{ts}.json"
    Path(out_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Validation saved -> {out_path}")

    print_readable(result)


if __name__ == "__main__":
    main()
