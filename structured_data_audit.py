"""
structured_data_audit.py
-------------------------
Standalone demo: audits the business's own website homepage for the
structured data (schema.org JSON-LD) and machine-parseable text that AI
answer engines rely on to understand and cite a business. Not wired into
the main trend pipeline.

Website URL resolution order:
  1. business_profile.json's "website" field (added by parse_business.py)
  2. business_website.json's "website" field (manual override -- fill this
     in if business_profile.json doesn't have a website yet)

Checks:
  - JSON-LD blocks (<script type="application/ld+json">) and which
    schema.org @type values they declare
  - Presence of business-identity types (LocalBusiness, Restaurant,
    FoodEstablishment, Store, Organization, ...), review types
    (Review, AggregateRating), FAQPage, and Product
  - Whether the page has enough plain, visible text to be readable by a
    text-based crawler at all (vs. being image/JS-only with no fallback)

Output: structured_data_audit_<timestamp>.json

Usage:
    python structured_data_audit.py
"""

import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BUSINESS_PROFILE_PATH = "business_profile.json"
BUSINESS_WEBSITE_PATH = "business_website.json"

REQUEST_TIMEOUT = 15  # seconds
TEXT_SAMPLE_CHARS = 3000
MIN_MEANINGFUL_TEXT_CHARS = 200
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE)
PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{7,}\d)")

# Category -> schema.org @type values that satisfy it.
SCHEMA_CATEGORIES = {
    "business_identity": [
        "LocalBusiness", "Restaurant", "FoodEstablishment",
        "CafeOrCoffeeShop", "BarOrPub", "Store", "Organization",
        "ProfessionalService", "Corporation",
    ],
    "reviews": ["Review", "AggregateRating"],
    "faq": ["FAQPage"],
    "product": ["Product", "Offer"],
}

# Rough industry -> most-relevant identity type, just for the note text.
INDUSTRY_TYPE_HINTS = [
    (("restaurant", "bbq", "food", "cafe", "bakery", "dessert"),
     "Restaurant or FoodEstablishment"),
    (("retail", "store", "shop", "ecommerce", "boutique"),
     "Store or Product"),
    (("skincare", "beauty", "cosmetic"), "Store or Product"),
    (("service", "consult", "agency", "studio"), "ProfessionalService"),
]


def load_json(path):
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def resolve_website(business):
    """Find the website URL to audit. Returns (url, source_description)."""
    if business:
        website = business.get("website")
        if website and str(website).strip():
            return str(website).strip(), BUSINESS_PROFILE_PATH

    override = load_json(BUSINESS_WEBSITE_PATH)
    if override:
        website = override.get("website")
        if website and str(website).strip():
            return str(website).strip(), BUSINESS_WEBSITE_PATH

    return None, None


def extract_visible_text(raw_html):
    no_script_style = SCRIPT_STYLE_RE.sub(" ", raw_html)
    no_tags = TAG_RE.sub(" ", no_script_style)
    unescaped = html.unescape(no_tags)
    return WHITESPACE_RE.sub(" ", unescaped).strip()


def extract_jsonld_types(raw_html):
    """Find all JSON-LD blocks and collect distinct @type values.

    Returns (types_found: set[str], blocks_found: int, parse_errors: int).
    """
    types_found = set()
    blocks = JSONLD_RE.findall(raw_html)
    parse_errors = 0

    def collect_types(obj):
        if isinstance(obj, dict):
            t = obj.get("@type")
            if isinstance(t, str):
                types_found.add(t)
            elif isinstance(t, list):
                types_found.update(x for x in t if isinstance(x, str))
            graph = obj.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    collect_types(item)
        elif isinstance(obj, list):
            for item in obj:
                collect_types(item)

    for block in blocks:
        try:
            data = json.loads(block.strip())
            collect_types(data)
        except json.JSONDecodeError:
            parse_errors += 1

    return types_found, len(blocks), parse_errors


def industry_type_hint(industry, sub_industry):
    combined = f"{industry or ''} {sub_industry or ''}".lower()
    for keywords, hint in INDUSTRY_TYPE_HINTS:
        if any(k in combined for k in keywords):
            return hint
    return "LocalBusiness (generic)"


def fetch_homepage(url):
    """Fetch the homepage HTML. Returns (html_text, error_or_None)."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text, None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError as e:
        return None, f"connection error: {e}"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        return None, f"http error: {status}"
    except requests.exceptions.RequestException as e:
        return None, f"request error: {e}"


def run_audit(url, business):
    raw_html, error = fetch_homepage(url)
    if error:
        return {
            "url": url,
            "fetched": False,
            "error": error,
        }

    types_found, block_count, parse_errors = extract_jsonld_types(raw_html)
    visible_text = extract_visible_text(raw_html)
    text_length = len(visible_text)
    has_meaningful_text = text_length >= MIN_MEANINGFUL_TEXT_CHARS

    category_status = {}
    for category, valid_types in SCHEMA_CATEGORIES.items():
        matched = sorted(types_found & set(valid_types))
        category_status[category] = {
            "present": bool(matched),
            "matched_types": matched,
        }

    business_name = (business or {}).get("business_name", "")
    industry = (business or {}).get("industry", "")
    sub_industry = (business or {}).get("sub_industry", "")

    text_lower = visible_text.lower()
    contains_business_name = bool(
        business_name and business_name.lower() in text_lower)
    contains_phone_like_number = bool(PHONE_RE.search(visible_text))

    notes = []
    if block_count == 0:
        notes.append(
            "No JSON-LD structured data found at all. AI answer engines "
            "and search engines have to fall back to guessing from plain "
            "text, which is far less reliable than an explicit schema.org "
            "declaration."
        )
    if not category_status["business_identity"]["present"]:
        hint = industry_type_hint(industry, sub_industry)
        notes.append(
            f"No business-identity schema type found (e.g. {hint}). "
            f"This is usually the single highest-value fix -- it tells "
            f"answer engines what kind of business this is, where it is, "
            f"and how to contact it, in a format they can parse directly."
        )
    if not category_status["reviews"]["present"]:
        notes.append(
            "No Review/AggregateRating markup found. Even if the business "
            "has real reviews elsewhere (Google, Facebook), without "
            "Review/AggregateRating schema on the site itself, answer "
            "engines can't easily surface a rating for this business."
        )
    if not category_status["faq"]["present"]:
        notes.append(
            "No FAQPage markup found. FAQ schema is one of the more "
            "direct ways to get specific Q&A content picked up verbatim "
            "by answer engines."
        )
    if not category_status["product"]["present"]:
        notes.append(
            "No Product/Offer markup found. If specific products or menu "
            "items should be individually discoverable, this is missing."
        )
    if not has_meaningful_text:
        notes.append(
            f"Only {text_length} characters of visible text were found on "
            f"the page after stripping tags/scripts/styles. If the real "
            f"content is rendered client-side (JS) or is mostly images "
            f"with no text fallback (alt text, captions, etc.), text-based "
            f"crawlers -- which most answer engines still rely on -- may "
            f"see next to nothing."
        )
    if not contains_business_name:
        notes.append(
            "The business's own name doesn't appear in its own homepage "
            "text (or wasn't detected). Worth double-checking manually."
        )

    return {
        "url": url,
        "fetched": True,
        "jsonld_blocks_found": block_count,
        "jsonld_parse_errors": parse_errors,
        "schema_types_found": sorted(types_found),
        "categories": category_status,
        "text_check": {
            "visible_text_length": text_length,
            "has_meaningful_text": has_meaningful_text,
            "contains_business_name": contains_business_name,
            "contains_phone_like_number": contains_phone_like_number,
            "text_sample": visible_text[:TEXT_SAMPLE_CHARS],
        },
        "notes": notes,
    }


def main():
    print("Structured Data / AI Legibility Audit (standalone demo)")
    print("-" * 50)

    business = load_json(BUSINESS_PROFILE_PATH)
    if not business:
        print(f"  NOTE: {BUSINESS_PROFILE_PATH} not found. Continuing "
              f"without business context (industry hints won't be used).")

    url, source = resolve_website(business)
    if not url:
        print(f"\n  No website URL configured.")
        print(f"  Either add a \"website\" field to {BUSINESS_PROFILE_PATH} "
              f"(re-run parse_business.py after the source .txt mentions "
              f"a website), or fill in {BUSINESS_WEBSITE_PATH}.")
        sys.exit(1)

    print(f"  Website : {url}")
    print(f"  Source  : {source}\n")

    result = run_audit(url, business)

    if not result["fetched"]:
        print(f"  ERROR: Could not fetch the homepage ({result['error']}).")
        print(f"  Nothing to audit.")
        out = {
            "_meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "business_file": BUSINESS_PROFILE_PATH if business else None,
            },
            "audit": result,
        }
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = f"structured_data_audit_{ts}.json"
        Path(out_path).write_text(
            json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved -> {out_path}")
        sys.exit(1)

    print(f"  JSON-LD blocks found : {result['jsonld_blocks_found']}")
    if result["jsonld_parse_errors"]:
        print(f"  JSON-LD parse errors : {result['jsonld_parse_errors']}")
    print(f"  schema.org types     : "
          f"{', '.join(result['schema_types_found']) or '(none)'}")
    print()
    for category, status in result["categories"].items():
        mark = "OK  " if status["present"] else "MISS"
        print(f"  [{mark}] {category:<18} "
              f"{', '.join(status['matched_types']) or '(none found)'}")
    print()
    tc = result["text_check"]
    print(f"  Visible text length      : {tc['visible_text_length']} chars")
    print(f"  Has meaningful text      : {tc['has_meaningful_text']}")
    print(f"  Business name in text    : {tc['contains_business_name']}")
    print(f"  Phone-like number found  : {tc['contains_phone_like_number']}")

    if result["notes"]:
        print(f"\n  Notes:")
        for n in result["notes"]:
            print(f"    - {n}")

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "business_file": BUSINESS_PROFILE_PATH if business else None,
        },
        "audit": result,
    }
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = f"structured_data_audit_{ts}.json"
    Path(out_path).write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Saved -> {out_path}")


if __name__ == "__main__":
    main()
