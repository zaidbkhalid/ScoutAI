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
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from demo_mode import is_demo_mode, resolve_preset_key, load_mock
from ad_formats import resolve as resolve_formats, FORMATS_BY_KEY
from copy_playbook import (COPY_PRINCIPLES, COPY_EXAMPLES, checklist_text,
                           banned_list_text, find_banned, find_invented_offers)

load_dotenv()

OPENAI_API = "https://api.openai.com/v1/chat/completions"
# Override with SCOUTAI_COPY_MODEL in .env to try another model.
MODEL      = os.environ.get("SCOUTAI_COPY_MODEL", "gpt-4o")

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
  rather than inventing one. Offers suggested by the campaign (free trials, bundles,
  discounts) are NOT confirmed -- only offers in the business profile are real. Write any
  other offer as [OFFER].

DISCLOSURE AWARENESS:
As a general principle, advertising and sponsored content are widely expected -- and in many
jurisdictions required -- to be recognisable as such to the audience. Where a format involves
paid promotion, a creator posting on the business's behalf, incentivised reviews, or anything
a reader could mistake for independent opinion, note that in disclosure_note. Describe the
general principle and suggest making the commercial relationship clear; do NOT state specific
legal rules, statutes, agency requirements or jurisdictions as fact, and do not claim any
particular wording is legally sufficient. Leave disclosure_note out for formats where it
doesn't apply.

Return ONLY valid JSON. No markdown fences.

""" + COPY_PRINCIPLES + "\n" + COPY_EXAMPLES + """
EXPLORE BEFORE YOU CHOOSE: for every format, first write three genuinely different opening
hooks in "hook_options" -- different angles (the reader's moment, a sharp contrast, a
question they're already asking, a concrete detail) -- then build the copy on the strongest.

Never use these stock phrases (or close variants): """ + banned_list_text() + "."


# The editor pass: a second model call that scores the draft against the
# playbook's checklist and rewrites what falls short. Drafting and editing
# are different jobs -- asking one prompt to do both produces copy that
# is "fine"; a dedicated critique-then-rewrite step is where most of the
# quality comes from.
EDITOR_PROMPT = """You are a senior copy editor at a top creative agency, reviewing a junior
writer's draft for a small-business client. Most drafts you see are "fine" -- and fine copy
gets scrolled past. Your job is to make it specific and alive. Do not approve generic copy.

For EACH format in the draft, in this order:
1. Openings. If the first line starts with an imperative + "your" ("Transform your...",
   "Turn your...", "Sweeten your...", "Elevate your..."), a greeting ("Hi!", "Hey there!"),
   or a generic exclamation, REWRITE it so it starts inside the reader's moment, with a
   contrast, or with a question they already have. Use the draft's hook_options if one is
   stronger.
2. Adjectives. Replace vague praise ("delicious", "high-quality", "amazing", "effortlessly")
   with concrete details that ARE in the business profile -- products, places, audience.
3. Honesty. Compare every claim to the business profile and campaign. Remove any discount,
   price, offer, deadline, statistic or promise that isn't there -- use a [PLACEHOLDER].
4. Fix every problem listed under "Problems found by automated checks". These are objective
   failures (over limits, stock phrases, invented offers) and must not survive.
5. Then check the result against this list and keep improving anything weak:
""" + checklist_text() + """

Hard rules for your rewrite (same as the writer's):
- Do not invent facts: no prices, dates, discounts, stats, reviews or guarantees that the
  business profile doesn't contain. Use [PLACEHOLDERS] instead.
- Respect every character limit exactly. Count characters.
- Keep the same campaign idea and offer across all formats.
- Never use these phrases: """ + banned_list_text() + """

Return ONLY valid JSON, no markdown fences, in this shape:
{
  "formats": [ ...the final, edited formats, in the SAME schema and order as the draft
               (you may drop hook_options)... ],
  "review": {
    "changes": ["one short line per meaningful edit, saying what changed and why, e.g.
                 'Instagram: opened on the 7pm toss instead of a generic exclamation'"]
  }
}"""


# Hard limits we can check in code instead of trusting the model to count.
# (field, max, unit) -- unit is "chars" or "words"; list fields check each item.
LIMITS = {
    "google_search_ad":   [("variants", 30, "chars"), ("descriptions", 90, "chars")],
    "x_post":             [("primary_text", 280, "chars")],
    "sms":                [("primary_text", 160, "chars")],
    "meta_ad":            [("headline", 40, "chars")],
    "pinterest_pin":      [("headline", 100, "chars"), ("primary_text", 500, "chars")],
    "email_campaign":     [("variants", 60, "chars")],
    "whatsapp_broadcast": [("primary_text", 70, "words")],
}
FIELD_NAMES = {"variants": "headline", "descriptions": "description",
               "primary_text": "text", "headline": "headline"}


def _measure(value, unit):
    return len(str(value)) if unit == "chars" else len(str(value).split())


def block_text(block):
    parts = [block.get("headline") or "", block.get("primary_text") or "",
             " ".join(block.get("variants") or []),
             " ".join(block.get("descriptions") or []),
             " ".join(b.get("content", "") for b in block.get("script_beats") or []),
             block.get("call_to_action") or ""]
    return "\n".join(p for p in parts if p)


def find_problems(result, brief=""):
    """Objective failures per format: over-limit lines, banned phrases and
    offers the brief never made."""
    problems = []
    for block in result.get("formats", []):
        key = block.get("format", "")
        for field, limit, unit in LIMITS.get(key, []):
            value = block.get(field)
            items = value if isinstance(value, list) else ([value] if value else [])
            for i, item in enumerate(items, 1):
                size = _measure(item, unit)
                if size > limit:
                    name = FIELD_NAMES.get(field, field)
                    which = f"{name} {i}" if isinstance(value, list) else name
                    problems.append(f'{key}: {which} is {size} {unit} (limit {limit}): "{item}"')
        for phrase in find_banned(block_text(block)):
            problems.append(f'{key}: uses the stock phrase "{phrase}"')
        if brief:
            for offer in find_invented_offers(block_text(block), brief):
                problems.append(f'{key}: mentions "{offer}", which the business never offered -- remove it or use [OFFER]')
    return problems


def enforce_limits(result):
    """Last line of defence after editing: drop over-limit list items when
    enough good ones remain (an over-limit search headline is rejected by
    the platform anyway), and flag anything still over in the notes."""
    for block in result.get("formats", []):
        key = block.get("format", "")
        flags = []
        for field, limit, unit in LIMITS.get(key, []):
            value = block.get(field)
            name = FIELD_NAMES.get(field, field)
            if isinstance(value, list):
                ok = [v for v in value if _measure(v, unit) <= limit]
                if len(ok) < len(value):
                    if len(ok) >= 2:
                        block[field] = ok
                    else:
                        flags.append(f"some {name}s are over the {limit}-{unit[:-1]} limit")
            elif value and _measure(value, unit) > limit:
                flags.append(f"the {name} is over the {limit}-{unit[:-1]} limit -- trim before publishing")
        if flags:
            note = "Check: " + "; ".join(flags) + "."
            block["notes"] = f"{block['notes']} {note}" if block.get("notes") else note
    return result


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
      "hook_options": ["three genuinely different opening hooks you considered"],
      "headline": "short headline / subject preview / title, where the format calls for one",
      "variants": ["alternative headlines, subject lines, thread parts or bullets"],
      "descriptions": ["Google Search ad descriptions only, max 90 characters each"],
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


def _supports_temperature(model):
    # Newer reasoning models only accept the default temperature.
    return not model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def call_model(system, user, temperature, timeout=180):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set. Add it to your .env file.")
    body = {
        "model": MODEL,
        **({"temperature": temperature} if _supports_temperature(MODEL) else {}),
        # Guarantees parseable JSON instead of hoping for it.
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    for attempt in range(3):
        resp = requests.post(
            OPENAI_API,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            json=body, timeout=timeout)
        # A 429 is either a short rate limit (worth waiting out) or an empty
        # credit balance (no point retrying -- surface it straight away).
        if resp.status_code == 429 and attempt < 2:
            try:
                code = resp.json().get("error", {}).get("code", "")
            except ValueError:
                code = ""
            if code not in ("insufficient_quota", "credit_balance_exhausted"):
                time.sleep(5 * (attempt + 1))
                continue
        break
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def edit(business, campaign, formats, draft):
    """Editor pass. Returns the edited result, or the draft unchanged if
    the edit fails -- a failed polish must never cost the user their copy."""
    # Offers are checked against the business profile only: a campaign may
    # suggest an offer, but only the owner can confirm one exists.
    brief = json.dumps(business, ensure_ascii=False)
    problems = find_problems(draft, brief)
    requirements = "\n".join(f"- {f['key']} ({f['label']}): {f['guidance']}" for f in formats)
    problem_text = "\n".join("- " + p for p in problems) if problems else "- none"
    user = f"""## Business Profile
{json.dumps(business, indent=2, ensure_ascii=False)}

## Campaign
{json.dumps(campaign, indent=2, ensure_ascii=False)}

## Format requirements
{requirements}

## Problems found by automated checks
{problem_text}

## Draft to edit
{json.dumps(draft, indent=2, ensure_ascii=False)}"""
    try:
        edited = call_model(EDITOR_PROMPT, user, temperature=0.4)
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  Editor pass failed ({e}); keeping the first draft.")
        return draft
    if not edited.get("formats"):
        print("  Editor pass returned nothing usable; keeping the first draft.")
        return draft
    edited.setdefault("campaign_name", draft.get("campaign_name"))
    review = edited.setdefault("review", {})
    review["automated_problems_found"] = problems
    review["automated_problems_remaining"] = find_problems(edited, brief)
    for block in edited["formats"]:
        block.pop("hook_options", None)
    return edited


def generate(business, campaign, formats, use_editor=True):
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

    # Copy benefits from more range than analysis; the editor reins it in.
    draft = call_model(SYSTEM_PROMPT, build_user_prompt(business, campaign, formats),
                       temperature=0.8)
    if not use_editor:
        for block in draft.get("formats", []):
            block.pop("hook_options", None)
        return enforce_limits(draft)
    print("  First draft written; running the editor pass...")
    return enforce_limits(edit(business, campaign, formats, draft))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", default=SELECTED_CAMPAIGN_PATH,
                        help='Path to selected_campaign.json '
                             '({"campaign": {...}, "formats": [...]})')
    parser.add_argument("--business", default=BUSINESS_PROFILE_PATH)
    parser.add_argument("--formats", default="",
                        help="Comma-separated format keys (overrides the file)")
    parser.add_argument("--no-edit", action="store_true",
                        help="Skip the editor pass (faster, cheaper, rougher)")
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
        result = generate(business, campaign, formats, use_editor=not args.no_edit)
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
    changes = (result.get("review") or {}).get("changes") or []
    if changes:
        print(f"  Editor made {len(changes)} change(s):")
        for c in changes[:8]:
            print(f"    - {c}")
        print()
    for block in result.get("formats", []):
        label = block.get("label") or block.get("format")
        preview = (block.get("primary_text") or "")[:70].replace("\n", " ")
        print(f"    {label}: {preview}...")


if __name__ == "__main__":
    main()
