"""
parse_business.py
─────────────────
Reads any business description .txt file and extracts a structured
profile using OpenAI. Output is saved as business_profile.json.

Usage:
    python3 parse_business.py my_business.txt
"""

import sys
import os
from dotenv import load_dotenv
load_dotenv()
import json
import requests
from pathlib import Path
from datetime import datetime


OPENAI_API = "https://api.openai.com/v1/chat/completions"
MODEL      = "gpt-4o"


SYSTEM_PROMPT = """You are a business analyst. Given a free-form business description, 
extract a structured profile. Return ONLY valid JSON, no preamble, no markdown fences.

The JSON must follow this exact schema:
{
  "business_name": "string or null",
  "website": "string URL or null (only if a website is explicitly mentioned in the text)",
  "location": "string or null (city/region/country the business is based in or serves, if mentioned -- a real place name, not a category)",
  "industry": "string",
  "sub_industry": "string",
  "products_or_services": ["list of strings"],
  "target_audience": {
    "demographics": "string describing age, gender, location etc",
    "psychographics": "string describing lifestyle, values, interests",
    "pain_points": ["list of strings"]
  },
  "brand_tone": ["list: e.g. professional, witty, inspirational, edgy, warm, bold"],
  "competitors_or_market": "string",
  "marketing_channels": ["list of current or desired channels"],
  "unique_value_proposition": "string",
  "business_stage": "idea | early-stage | growth | established",
  "geographic_focus": "local | national | global",
  "additional_context": "any other relevant info from the text"
}

Be liberal in inference for subjective fields (tone, stage, audience, etc.) -- if something is not
stated, make a reasonable guess based on context. The exceptions are "website" and "location": these
are factual fields, so only fill them in if the text actually states them. Never invent a URL or a
place name -- use null instead of guessing."""


def parse_business_txt(filepath: str) -> dict:
    text = Path(filepath).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Business description file is empty.")

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable not set.\nRun: export OPENAI_API_KEY='sk-...'")

    payload = {
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"Here is the business description:\n\n{text}"}
        ]
    }

    resp = requests.post(
        OPENAI_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown fences if model adds them anyway
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    profile = json.loads(raw)
    profile["_source_file"] = str(filepath)
    profile["_parsed_at"]   = datetime.now().isoformat()
    return profile


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 parse_business.py <business_description.txt>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"Parsing: {filepath}\n")

    try:
        profile = parse_business_txt(filepath)
    except requests.exceptions.HTTPError as e:
        detail = ""
        if e.response is not None:
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = e.response.text[:300]
        print(f"ERROR: OpenAI API request failed: {e}")
        if detail:
            print(f"   Detail: {detail}")
        sys.exit(1)
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    out_path = "business_profile.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    print(f"✅ Business profile saved → {out_path}\n")
    print(f"  Business  : {profile.get('business_name') or '(unnamed)'}")
    print(f"  Industry  : {profile.get('industry')}")
    print(f"  Tone      : {', '.join(profile.get('brand_tone', []))}")
    print(f"  Audience  : {profile.get('target_audience', {}).get('demographics')}")


if __name__ == "__main__":
    main()