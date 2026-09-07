"""
campaign_ideas.py
-------------------
Stage 2 of the trend pipeline: turn trends the owner selected in Stage 1
(trend_analyzer.py) into broader marketing campaign concepts.

Deliberately still one level up from execution -- a campaign name/theme,
the core strategic idea, who it's for, and 2-3 directions to execute it.
Not specific ad copy yet. That's Stage 3 (ad_copy.py), generated per
campaign once the owner picks one and a format (email, Instagram post,
short video, etc.).

Reads selected_trends.json ({"trends": [...]}) -- each entry is whatever
shape Stage 1 produced for it (the business-relevance shape with
why_people_care/bridge_to_business/timing/lifecycle, or the data-only
shape with just entity_name/source/score/velocity/age_hours -- both are
handled; a data-only trend just means the model reasons about relevance
itself here instead of reusing a Stage 1 judgment).

Output: campaign_ideas_<timestamp>.json

Usage:
    python campaign_ideas.py
    python campaign_ideas.py --trends selected_trends.json
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from demo_mode import is_demo_mode, resolve_preset_key, load_mock

load_dotenv()

OPENAI_API = "https://api.openai.com/v1/chat/completions"
MODEL      = "gpt-4o"

BUSINESS_PROFILE_PATH = "business_profile.json"
SELECTED_TRENDS_PATH = "selected_trends.json"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def normalize_trend(t):
    """Accept either Stage 1 shape (business-relevance) or the data-only
    top_trends shape, and normalize to a common dict for the prompt."""
    name = t.get("trend") or t.get("entity_name") or ""
    return {
        "trend": name,
        "source": t.get("source", ""),
        "why_people_care": t.get("why_people_care"),
        "bridge_to_business": t.get("bridge_to_business"),
        "relevance_score": t.get("relevance_score"),
        "timing": t.get("timing"),
        "lifecycle": t.get("lifecycle"),
        "score": t.get("score"),
        "velocity": t.get("velocity"),
        "age_hours": t.get("age_hours"),
        "is_new": t.get("is_new"),
    }


SYSTEM_PROMPT = """You are a marketing campaign strategist for a small business. You'll be
given a business profile and a handful of trends the owner specifically chose to build on.
Some trends already carry a relevance judgment (why people care, the creative bridge, a
relevance score) from an earlier pass; others are just raw trending signals with no judgment
attached yet -- for those, use your own reasoning for why (or whether) they're worth a
campaign, and say plainly if the connection is a stretch.

For each trend, propose ONE broader marketing campaign concept -- not a single product, and
not specific ad copy yet. A campaign is the umbrella idea: a name/theme, the core strategic
idea, who specifically it's aimed at and why now, and 2-3 concrete directions it could be
executed in (short directions, not full copy -- that comes later).

Be specific to this business -- its tone, audience, products, and location. Never say
"consider running a campaign around X." Say what the campaign actually is.

Return ONLY valid JSON. No markdown fences."""


def build_user_prompt(business, trends):
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## Trends Selected by the Owner
{json.dumps(trends, indent=2)}

Propose one campaign concept per trend above. Return this exact JSON structure:
{{
  "campaigns": [
    {{
      "trend": "trend name, copied exactly from the input",
      "campaign_name": "short, memorable campaign name/theme",
      "core_idea": "the strategic concept in 1-2 sentences",
      "target_audience_angle": "who this specifically speaks to, and why now",
      "timing": "act now|this week|plan ahead",
      "execution_directions": [
        {{
          "direction": "a concrete way to execute this campaign",
          "why_it_works": "why this direction fits the business and this trend"
        }}
      ]
    }}
  ]
}}"""


def generate(business, trends):
    # -- DEMO_MODE: skip the real API call for the 3 built-in synthetic
    # businesses, using a hand-written canned set of campaigns instead.
    # Shows a fixed example set regardless of which trends were actually
    # selected -- same limitation as every other DEMO_MODE intercept in
    # this project. See demo_mode.py. Falls through to the real call
    # below for anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business.get("business_name", ""))
        mock = load_mock(preset_key, "campaign_ideas.json")
        if mock:
            print(f"  [DEMO_MODE] Using canned campaign ideas for "
                  f"'{business.get('business_name')}' -- no API call made. "
                  f"(Fixed example set, not built from what you selected.)")
            return mock
    # -- end DEMO_MODE --

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set. Add it to your .env file.")

    resp = requests.post(
        OPENAI_API,
        headers={
            "Content-Type" : "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        json={
            "model"      : MODEL,
            "temperature": 0.5,
            "messages"   : [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": build_user_prompt(business, trends)}
            ]
        },
        timeout=90
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trends", default=SELECTED_TRENDS_PATH,
                        help="Path to selected_trends.json ({\"trends\": [...]})")
    parser.add_argument("--business", default=BUSINESS_PROFILE_PATH)
    args = parser.parse_args()

    print("Campaign Ideas (Stage 2: trends -> campaign concepts)")
    print("-" * 40)

    if not Path(args.business).exists():
        print(f"  {args.business} not found.")
        print("  Run: python parse_business.py my_business.txt")
        sys.exit(1)
    business = load_json(args.business)

    if not Path(args.trends).exists():
        print(f"  {args.trends} not found.")
        print("  Select trends from trend_analyzer.py's output and write "
              f'them to {args.trends} as {{"trends": [...]}}.')
        sys.exit(1)
    raw_trends = load_json(args.trends).get("trends", [])
    if not raw_trends:
        print("  No trends selected.")
        sys.exit(1)

    trends = [normalize_trend(t) for t in raw_trends]

    print(f"  Business : {args.business}")
    print(f"  Trends   : {len(trends)} selected\n")

    try:
        result = generate(business, trends)
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
        "generated_at": datetime.now().isoformat(),
        "business_file": str(Path(args.business).name),
        "trend_count": len(trends),
    }

    out = f"campaign_ideas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"  Saved -> {out}\n")

    campaigns = result.get("campaigns", [])
    if campaigns:
        print("  Campaigns:")
        for c in campaigns:
            print(f"     {c.get('campaign_name')} -- built on '{c.get('trend')}' "
                  f"[{c.get('timing')}]")

    print(f"\n  Next: pick a campaign and a format to generate specific ad copy.")


if __name__ == "__main__":
    main()
