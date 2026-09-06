"""
ai_legibility_synthesis.py
----------------------------
Standalone demo: combines the structured-data audit and the AI visibility
check into one readable picture of how legible this business currently is
to AI answer engines, with concrete (but non-guaranteed) fixes to consider.
Not wired into the main trend pipeline.

Reads the latest structured_data_audit_*.json and ai_visibility_*.json.

Output: ai_legibility_report_<timestamp>.json

Usage:
    python ai_legibility_synthesis.py
"""

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

SYSTEM_PROMPT = """You are a consultant helping a small business owner understand how visible \
their business currently is to AI answer engines (Perplexity, ChatGPT search, Google AI \
Overviews, etc.) and what's driving that.

You'll be given two things: a structured-data audit of the business's own website (what \
schema.org markup it does or doesn't have, whether the page has real crawlable text), and a set \
of natural customer-style questions that were actually sent to an AI answer engine, with whether \
the business was mentioned in each answer and what was cited instead when it wasn't.

Describe what's currently making this business legible or invisible to AI assistants and answer \
engines. Be specific: name the missing schema types, name the queries where the business didn't \
show up and who/what got cited instead.

Do not claim any fix will guarantee improved visibility or ranking. Present recommendations as \
things worth considering and testing, not verdicts. If the input data is too thin to say something \
meaningful about a section, say so plainly instead of inventing detail.

Return ONLY valid JSON. No markdown fences."""


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def build_user_prompt(business, audit, visibility):
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## Structured Data Audit (this business's own website)
{json.dumps(audit, indent=2)}

## AI Visibility Check (real queries sent to an AI answer engine)
{json.dumps(visibility, indent=2)}

Analyze the two datasets above together.

Return this exact JSON structure:
{{
  "legibility_summary": "one paragraph on how legible/visible this business currently is to AI answer engines and why",
  "structured_data_gaps": [
    {{
      "missing": "the missing schema type or text issue",
      "why_it_matters": "why this specifically affects AI legibility"
    }}
  ],
  "visibility_gaps": [
    {{
      "query": "the query that was tested",
      "what_happened": "not mentioned, cited instead: X / partial match / etc.",
      "note": "brief interpretation"
    }}
  ],
  "recommended_fixes": [
    {{
      "fix": "a concrete, specific fix to consider",
      "reasoning": "why this looks worth testing, based on the gaps above"
    }}
  ],
  "caveat": "a short reminder that this is based on a limited automated check, and that no fix here is a guarantee of improved visibility"
}}"""


def analyze(business, audit, visibility):
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
                {"role": "user", "content": build_user_prompt(business, audit, visibility)},
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
    print("  AI LEGIBILITY REPORT")
    print("=" * 60)

    summary = result.get("legibility_summary", "")
    if summary:
        print(f"\n  {summary}")

    gaps = result.get("structured_data_gaps", [])
    if gaps:
        print(f"\n  Structured data gaps:")
        for g in gaps:
            print(f"    - {g.get('missing', '')}")
            print(f"      Why it matters: {g.get('why_it_matters', '')}")

    vgaps = result.get("visibility_gaps", [])
    if vgaps:
        print(f"\n  Visibility gaps:")
        for v in vgaps:
            print(f"    - \"{v.get('query', '')}\" -> {v.get('what_happened', '')}")
            note = v.get("note", "")
            if note:
                print(f"      {note}")

    fixes = result.get("recommended_fixes", [])
    if fixes:
        print(f"\n  Recommended fixes to consider:")
        for f in fixes:
            print(f"    - {f.get('fix', '')}")
            print(f"      Reasoning: {f.get('reasoning', '')}")

    caveat = result.get("caveat", "")
    if caveat:
        print(f"\n  Caveat: {caveat}")
    print()


def main():
    print("AI Legibility Synthesis (standalone demo)")
    print("-" * 40)

    if not Path(BUSINESS_PROFILE_PATH).exists():
        print(f"  {BUSINESS_PROFILE_PATH} not found.")
        print(f"  Run: python parse_business.py my_business.txt")
        sys.exit(1)
    business = load_json(BUSINESS_PROFILE_PATH)

    audit_path = find_latest("structured_data_audit_*.json")
    visibility_path = find_latest("ai_visibility_*.json")

    missing = []
    if not audit_path:
        missing.append("structured_data_audit_*.json (run structured_data_audit.py)")
    if not visibility_path:
        missing.append("ai_visibility_*.json (run ai_visibility_check.py)")
    if missing:
        print("  Missing input file(s):")
        for m in missing:
            print(f"    - {m}")
        sys.exit(0)

    print(f"  Business   : {BUSINESS_PROFILE_PATH}")
    print(f"  Audit      : {audit_path}")
    print(f"  Visibility : {visibility_path}\n")

    audit = load_json(audit_path)
    visibility = load_json(visibility_path)

    if not audit.get("audit", {}).get("fetched", False):
        print("  The structured data audit didn't successfully fetch the "
              "website, so there's limited signal on that side. "
              "Continuing anyway -- the visibility check data is still useful.")

    if visibility.get("_meta", {}).get("succeeded", 0) == 0:
        print("\n  No AI visibility queries succeeded, so there's nothing "
              "meaningful to synthesize on that side either.")
        print("  Check PERPLEXITY_API_KEY and re-run ai_visibility_check.py.")
        sys.exit(0)

    print("  Sending both datasets to GPT-4o...\n")
    try:
        result = analyze(business, audit, visibility)
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
        "business_file": str(Path(BUSINESS_PROFILE_PATH).name),
        "audit_file": str(Path(audit_path).name),
        "visibility_file": str(Path(visibility_path).name),
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = f"ai_legibility_report_{ts}.json"
    Path(out_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Report saved -> {out_path}")

    print_readable(result)


if __name__ == "__main__":
    main()
