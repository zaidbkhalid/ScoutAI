"""
ad_copy.py
----------
Stage 3 of the trend pipeline: a chosen campaign concept -> actual,
publishable copy in the specific formats the user asks for.

Stage 1 (trend_analyzer.py) says what's worth acting on.
Stage 2 (campaign_ideas.py) turns a trend into a campaign concept.
Stage 3 (this) writes the copy for that concept, per platform.

Formats come from ad_formats.py, which carries the practical constraints
per platform (Google's 30-character headline cap, the shape of a short
video script, where an email subject line goes). One model call covers
every requested format so they stay consistent with each other -- three
separate calls would drift in voice and offer.

Reads:
    selected_campaign.json  {"campaign": {...}, "formats": ["instagram_post", ...]}
    business_profile.json

Output: ad_copy_<timestamp>.json

Usage:
    python ad_copy.py
    python ad_copy.py --campaign selected_campaign.json --formats instagram_post,email_campaign
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
from ad_formats import resolve as resolve_formats, FORMATS_BY_KEY

load_dotenv()

OPENAI_API = "https://api.openai.com/v1/chat/completions"
MODEL      = "gpt-4o"

SELECTED_CAMPAIGN_PATH = "selected_campaign.json"
BUSINESS_PROFILE_PATH  = "business_profile.json"


SYSTEM_PROMPT = """You are a direct-response copywriter for a small business. You will be given a
business profile, one campaign concept that was already agreed, and a list of formats to write.

Write copy that is actually publishable as-is. Not a description of what the copy should do --
the copy itself, in the business's own voice, specific to their products and audience.

Rules:
- Respect every stated constraint, especially hard character limits. Copy that exceeds a
  platform's limit is unusable, so count characters before returning them.
- Stay in the business's brand tone. A playful bakery and a B2B bookkeeping tool should not
  sound alike.
- Be concrete. Name the actual product, the actual offer, the actual place. Generic copy
  ("elevate your experience", "unlock your potential") is a failure.
- Every format must serve the SAME campaign concept and the same offer. They are one campaign
  expressed in different places, not unrelated ideas.
- Do not invent facts about the business: no fake discounts, prices, dates, guarantees,
  statistics, awards or testimonials that were not provided. If a piece of copy needs a
  specific number or date to work, leave a clearly-marked placeholder like [PRICE] or [DATE]
  rather than inventing one.

DISCLOSURE AWARENESS:
As a general principle, advertising and sponsored content are widely expected -- and in many
jurisdictions required -- to be recognisable as such to the audience. Where a format involves
paid promotion, a creator posting on the business's behalf, incentivised reviews, or anything
a reader could mistake for independent opinion, note that in disclosure_note. Describe the
general principle and suggest making the commercial relationship clear; do NOT state specific
legal rules, statutes, agency requirements or jurisdictions as fact, and do not claim any
particular wording is legally sufficient. Leave disclosure_note out for formats where it
doesn't apply.

Return ONLY valid JSON. No markdown fences."""


def build_user_prompt(business, campaign, formats):
    format_specs = "\n".join(
        f"- {f['key']} ({f['label']}): {f['guidance']}" for f in formats
    )
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## The Campaign Concept (already chosen -- write copy for THIS)
{json.dumps(campaign, indent=2)}

## Formats To Write
{format_specs}

Return this exact JSON structure, with one entry in "formats" for each format key above,
in the same order. Include only the fields that make sense for that format and omit the rest
-- an email has no hashtags, a poster has no script beats:

{{
  "campaign_name": "the campaign this copy serves",
  "formats": [
    {{
      "format": "the format key, copied exactly",
      "primary_text": "the main body copy, ready to publish",
      "headline": "short headline / subject preview / title, where the format calls for one",
      "variants": ["alternative headlines, subject lines, thread parts or bullets"],
      "script_beats": [
        {{"beat": "Hook (0-2s)", "content": "what is said/shown on screen"}}
      ],
      "hashtags": ["#only", "#where", "#relevant"],
      "call_to_action": "the single action you want the reader to take",
      "notes": "one practical note on using this -- timing, targeting, what to pair it with",
      "disclosure_note": "only where paid promotion or a creator relationship is involved"
    }}
  ]
}}"""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def generate(business, campaign, formats):
    # -- DEMO_MODE: skip the real API call for the 3 built-in synthetic
    # businesses, using hand-written canned copy instead. See demo_mode.py.
    # Falls through to the real call below for anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business.get("business_name", ""))
        mock = load_mock(preset_key, "ad_copy.json")
        if mock:
            print(f"  [DEMO_MODE] Using canned ad copy for "
                  f"'{business.get('business_name')}' -- no API call made.")
            print(f"  NOTE: this is a fixed example set; it does not vary "
                  f"with the campaign or formats selected.")
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
            "temperature": 0.8,   # copy benefits from more range than analysis
            "messages"   : [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": build_user_prompt(
                    business, campaign, formats)}
            ]
        },
        timeout=180
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
    parser.add_argument("--campaign", default=SELECTED_CAMPAIGN_PATH,
                        help='Path to selected_campaign.json '
                             '({"campaign": {...}, "formats": [...]})')
    parser.add_argument("--business", default=BUSINESS_PROFILE_PATH)
    parser.add_argument("--formats", default="",
                        help="Comma-separated format keys (overrides the file)")
    args = parser.parse_args()

    print("Ad Copy (Stage 3: campaign concept -> publishable copy)")
    print("-" * 40)

    if not Path(args.business).exists():
        print(f"  {args.business} not found.")
        print("  Run: python parse_business.py my_business.txt")
        sys.exit(1)

    if not Path(args.campaign).exists():
        print(f"  {args.campaign} not found.")
        print('  Expected {"campaign": {...}, "formats": ["instagram_post", ...]}')
        sys.exit(1)

    business = load_json(args.business)
    payload  = load_json(args.campaign)

    campaign = payload.get("campaign") or {}
    if not campaign:
        print("  No campaign found in the selection file.")
        sys.exit(1)

    keys = ([k.strip() for k in args.formats.split(",") if k.strip()]
            or payload.get("formats") or [])
    formats = resolve_formats(keys)

    unknown = [k for k in keys if k not in FORMATS_BY_KEY]
    if unknown:
        print(f"  Ignoring unknown format(s): {', '.join(unknown)}")

    if not formats:
        print("  No valid formats requested.")
        print(f"  Available: {', '.join(FORMATS_BY_KEY)}")
        sys.exit(1)

    print(f"  Business : {business.get('business_name')}")
    print(f"  Campaign : {campaign.get('campaign_name')}")
    print(f"  Formats  : {len(formats)} -- "
          f"{', '.join(f['label'] for f in formats)}\n")

    try:
        result = generate(business, campaign, formats)
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

    # Label each block so the UI doesn't have to know the catalogue
    for block in result.get("formats", []):
        spec = FORMATS_BY_KEY.get(block.get("format"))
        if spec:
            block["label"] = spec["label"]
            block["group"] = spec["group"]

    result["_meta"] = {
        "generated_at": datetime.now().isoformat(),
        "business_file": str(Path(args.business).name),
        "campaign_name": campaign.get("campaign_name"),
        "formats_requested": [f["key"] for f in formats],
    }

    out = f"ad_copy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"  Saved -> {out}\n")
    for block in result.get("formats", []):
        label = block.get("label") or block.get("format")
        preview = (block.get("primary_text") or "")[:70].replace("\n", " ")
        print(f"    {label}: {preview}...")


if __name__ == "__main__":
    main()
