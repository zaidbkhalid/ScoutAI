"""
image_generation.py
---------------------
Prototype: turns a campaign idea (or, failing that, a trend + business
pairing) into an actual generated marketing image via OpenAI's Images
API. Standalone for now -- not wired into app.py or the web UI.

Model: gpt-image-1-mini -- confirmed against OpenAI's current API docs
before building this (image model names have shifted: gpt-image-1,
gpt-image-1.5, gpt-image-1-mini, gpt-image-2 all currently exist).
gpt-image-1-mini is deliberately the cheapest suitable tier for this
prototype (~$0.005/image at "low" quality, 1024x1024), not the flagship
model. Endpoint: POST https://api.openai.com/v1/images/generations.

DEMO_MODE: for the 3 built-in synthetic businesses, skips the real API
call entirely and returns a clearly-labeled placeholder image instead --
a plain, procedurally generated stand-in (diagonal stripes, the standard
"missing asset" convention), not a real AI generation and not passed off
as one. Generated once per preset into mock_data/<preset>/placeholder.png
using only the standard library (no new dependency for a throwaway
prototype asset).

Usage:
    python image_generation.py
    python image_generation.py --campaign campaign_ideas_20260908_120000.json --index 0
"""

import os
import sys
import json
import base64
import struct
import zlib
import glob
import argparse
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from demo_mode import is_demo_mode, resolve_preset_key

load_dotenv()

IMAGES_API = "https://api.openai.com/v1/images/generations"
MODEL = "gpt-image-1-mini"
DEFAULT_SIZE = "1024x1024"
DEFAULT_QUALITY = "low"

# Published per-token rates for gpt-image-1-mini, per OpenAI's pricing page
# at the time this was built -- used only to *estimate* a dollar cost from
# the token usage the API actually reports. The API response itself does
# not include a dollar figure, only token counts.
PRICE_PER_1M_INPUT_TOKENS = 2.50
PRICE_PER_1M_OUTPUT_TOKENS = 8.00

MOCK_DATA_DIR = Path(__file__).parent / "mock_data"

# Loose brand-ish two-color pairs per preset, just so the 3 placeholders
# aren't visually identical -- not meant to resemble real brand assets.
PRESET_COLORS = {
    "velvet":  ((240, 200, 210), (250, 235, 238)),   # soft pink / blush
    "firepit": ((210, 90, 50),   (250, 220, 190)),   # charcoal-orange / cream
    "glowly":  ((150, 205, 200), (230, 248, 245)),   # mint / pale teal
}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


# ── Minimal, dependency-free PNG placeholder generator ──────────
# No Pillow/imaging library required -- this is a throwaway demo-mode
# asset, not worth a new project dependency for.

def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))


def write_placeholder_png(path, size=512, color_a=(200, 200, 200),
                           color_b=(230, 230, 230), stripe=48):
    """Writes a plain diagonal-stripe PNG -- the standard 'placeholder /
    missing asset' visual convention, deliberately unambiguous as a
    stand-in rather than real art."""
    width = height = size
    rows = []
    for y in range(height):
        row = bytearray([0])  # PNG filter type 0 (none) prefix
        for x in range(width):
            c = color_a if ((x + y) // stripe) % 2 == 0 else color_b
            row.extend(c)
        rows.append(bytes(row))
    raw = b"".join(rows)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(raw, 6)
    png = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def ensure_placeholder(preset_key):
    """Generate mock_data/<preset>/placeholder.png once if missing. Returns its path."""
    out_dir = MOCK_DATA_DIR / preset_key
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "placeholder.png"
    if not path.exists():
        color_a, color_b = PRESET_COLORS.get(
            preset_key, ((210, 210, 210), (235, 235, 235)))
        write_placeholder_png(path, size=512, color_a=color_a, color_b=color_b)
    return path


# ── Prompt construction ──────────────────────────────────────────

def build_image_prompt(business, campaign):
    tone = ", ".join(business.get("brand_tone", []) or []) or "clean, professional"
    industry = business.get("industry", "") or "small business"
    products = business.get("products_or_services", []) or []
    product_hint = products[0] if products else industry

    campaign_name = campaign.get("campaign_name", "")
    core_idea = campaign.get("core_idea", "")

    return (
        f"A marketing visual for a {industry} brand with a {tone} aesthetic. "
        f"Campaign concept: {campaign_name}. {core_idea} "
        f"Featuring: {product_hint}. "
        f"Style: high-quality lifestyle/product photography suitable for social "
        f"media marketing. No visible text, logos, or watermarks in the image."
    ).strip()


# ── Input loading: campaign idea, or fall back to a trend + business pairing ──

def load_campaign_or_trend(index=0, campaign_file=None):
    """Returns (item_dict, kind) where kind is 'campaign' or 'trend'."""
    path = campaign_file or find_latest("campaign_ideas_*.json")
    if path and Path(path).exists():
        data = load_json(path)
        campaigns = data.get("campaigns", [])
        if campaigns:
            idx = min(index, len(campaigns) - 1)
            return campaigns[idx], "campaign"

    path = find_latest("trend_analyzer_*.json")
    if path and Path(path).exists():
        data = load_json(path)
        trends = data.get("top_for_your_business", [])
        if trends:
            idx = min(index, len(trends) - 1)
            t = trends[idx]
            return {
                "trend": t.get("trend", ""),
                "campaign_name": (t.get("trend", "") or "Trend").title(),
                "core_idea": t.get("bridge_to_business", ""),
            }, "trend"

    return None, None


# ── Image generation ─────────────────────────────────────────────

def generate_image(business, campaign, job_dir=None):
    business_name = business.get("business_name", "")
    prompt = build_image_prompt(business, campaign)
    out_dir = Path(job_dir) if job_dir else Path(".")

    # -- DEMO_MODE: for the 3 built-in synthetic businesses, skip the real
    # API call entirely and use a clearly-labeled placeholder instead. See
    # demo_mode.py. Falls through to the real call below for anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business_name)
        if preset_key:
            print(f"  [DEMO_MODE] Using a placeholder image for "
                  f"'{business_name}' -- no API call made, no image "
                  f"actually generated.")
            placeholder_path = ensure_placeholder(preset_key)
            dest = out_dir / f"image_{preset_key}_placeholder.png"
            dest.write_bytes(placeholder_path.read_bytes())
            return {
                "is_placeholder": True,
                "image_path": str(dest),
                "prompt_used": prompt,
                "model": None,
                "note": "DEMO_MODE placeholder -- a plain procedurally "
                        "generated stand-in image, not a real AI generation. "
                        "No API call was made.",
            }
    # -- end DEMO_MODE --

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set. Add it to your .env file.")

    resp = requests.post(
        IMAGES_API,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": MODEL,
            "prompt": prompt,
            "size": DEFAULT_SIZE,
            "quality": DEFAULT_QUALITY,
            "n": 1,
        },
        timeout=120,
    )
    resp.raise_for_status()
    body = resp.json()

    b64 = body["data"][0]["b64_json"]
    image_bytes = base64.b64decode(b64)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"image_{ts}.png"
    dest.write_bytes(image_bytes)

    usage = body.get("usage")
    estimated_cost_usd = None
    if usage:
        in_tok = usage.get("input_tokens", 0) or 0
        out_tok = usage.get("output_tokens", 0) or 0
        estimated_cost_usd = round(
            (in_tok / 1_000_000) * PRICE_PER_1M_INPUT_TOKENS +
            (out_tok / 1_000_000) * PRICE_PER_1M_OUTPUT_TOKENS, 5
        )

    return {
        "is_placeholder": False,
        "image_path": str(dest),
        "prompt_used": prompt,
        "model": MODEL,
        "quality": DEFAULT_QUALITY,
        "size": DEFAULT_SIZE,
        "usage": usage,
        "estimated_cost_usd": estimated_cost_usd,
        "cost_note": "Estimated from the API's reported token usage using "
                      "OpenAI's published per-token rate for gpt-image-1-mini "
                      "-- the API response itself does not include a dollar figure.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", default="business_profile.json")
    parser.add_argument("--campaign", default=None,
                        help="Path to campaign_ideas_*.json (default: latest found)")
    parser.add_argument("--index", type=int, default=0,
                        help="Which campaign/trend to use if multiple are available")
    parser.add_argument("--job-dir", default=None,
                        help="Directory to save the image into (default: current directory)")
    args = parser.parse_args()

    print("Image Generation (prototype -- standalone, not wired into the web app)")
    print("-" * 40)

    if not Path(args.business).exists():
        print(f"  {args.business} not found.")
        print("  Run: python parse_business.py my_business.txt")
        sys.exit(1)
    business = load_json(args.business)

    item, kind = load_campaign_or_trend(args.index, args.campaign)
    if not item:
        print("  No campaign_ideas_*.json or trend_analyzer_*.json found.")
        print("  Run campaign_ideas.py (or trend_analyzer.py) first.")
        sys.exit(1)

    print(f"  Business : {business.get('business_name')}")
    print(f"  Source   : {kind} -- {item.get('campaign_name') or item.get('trend')}")
    print(f"  Model    : {MODEL} (quality={DEFAULT_QUALITY}, size={DEFAULT_SIZE})\n")

    try:
        result = generate_image(business, item, job_dir=args.job_dir)
    except requests.exceptions.HTTPError as e:
        detail = ""
        code = ""
        if e.response is not None:
            try:
                err = e.response.json().get("error", {})
                detail = err.get("message", "")
                code = (err.get("code") or err.get("type") or "")
            except ValueError:
                detail = e.response.text[:300]
        print(f"  ERROR: OpenAI Images API request failed: {e}")
        if detail:
            print(f"  Detail: {detail}")
        if code in ("insufficient_quota", "credit_balance_exhausted") or "quota" in detail.lower():
            print("  This is the current, expected state of the account "
                  "(no credits remaining) -- not a bug in this script. "
                  "Add credits and re-run.")
        sys.exit(1)
    except (requests.exceptions.RequestException, ValueError) as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    result["_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "business_file": str(Path(args.business).name),
        "source_kind": kind,
        "source_name": item.get("campaign_name") or item.get("trend"),
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = f"image_generation_{ts}.json"
    Path(out).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  Image saved  -> {result['image_path']}")
    print(f"  Record saved -> {out}")
    if result.get("is_placeholder"):
        print(f"  (placeholder -- not a real generation)")
    elif result.get("estimated_cost_usd") is not None:
        print(f"  Estimated cost: ${result['estimated_cost_usd']}")


if __name__ == "__main__":
    main()
