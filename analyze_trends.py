"""
analyze_trends.py
-----------------
The intelligence layer. Takes real trending topics from multiple platforms
and figures out how a business can capitalize on them --
even when the connection is not obvious.

This is NOT keyword matching. It's creative strategic mapping.

Reads snapshot_*.json files (shared schema) and optionally trend_scores_*.json
for computed freshness metrics.
"""

import os
import sys
import json
import glob
import argparse
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from demo_mode import is_demo_mode, resolve_preset_key, load_mock

load_dotenv()

OPENAI_API = "https://api.openai.com/v1/chat/completions"
MODEL      = "gpt-4o"


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def load_trends_from_snapshots(snapshot_paths):
    """Load all snapshot files and build a unified trend list.

    Groups entities by source and entity_type, creating one trend entry
    per logical group with appropriate metrics attached.
    """
    trends = []

    for path in snapshot_paths:
        if not Path(path).exists():
            continue
        data = load_json(path)
        source = data.get("source", "unknown")
        source_meta = data.get("source_meta", {})
        entities = data.get("entities", [])

        if source in ("google_trends",):
            # Keywords
            kw_entities = [
                e for e in entities if e["entity_type"] == "keyword"
            ]
            rq_entities = [
                e for e in entities if e["entity_type"] == "related_query"
            ]

            # Group related queries by parent_keyword
            rq_by_parent = {}
            for rq in rq_entities:
                parent = rq.get("raw", {}).get("parent_keyword", "")
                kind = rq.get("metrics", {}).get("query_kind", "rising")
                rq_by_parent.setdefault(parent, {"rising": [], "top": []})
                rq_by_parent[parent][kind].append(rq)

            for kw in kw_entities:
                kw_name = kw["entity_name"]
                parent_raw = kw.get("raw", {}).get("keyword", kw_name)
                rqs = rq_by_parent.get(parent_raw, {"rising": [], "top": []})
                rising = [r["entity_name"] for r in rqs.get("rising", [])[:5]]
                top = [r["entity_name"] for r in rqs.get("top", [])[:5]]

                trends.append({
                    "trend": kw_name,
                    "source": "google_trends",
                    "avg_interest": kw.get("metrics", {}).get(
                        "avg_interest", 0),
                    "rising_subtopics": rising,
                    "top_subtopics": top,
                })

        elif source == "google_trending_now":
            for item in entities:
                if item["entity_type"] != "trending_topic":
                    continue
                articles = [
                    a.get("title", "")
                    for a in item.get("raw", {}).get("articles", [])[:2]
                ]
                trends.append({
                    "trend": item["entity_name"],
                    "source": "google_realtime",
                    "related_news": articles,
                })

        elif source == "youtube_trending":
            yt_topics = []
            for item in entities:
                if item["entity_type"] != "video":
                    continue
                yt_topics.append({
                    "title": item.get("raw", {}).get("title",
                                                     item["entity_name"]),
                    "channel": item.get("raw", {}).get("channel", ""),
                    "views": item.get("metrics", {}).get("view_count", 0),
                    "category": item.get("category", ""),
                    "rank": item.get("rank"),
                })
            if yt_topics:
                trends.append({
                    "trend": "YouTube Trending Videos",
                    "source": "youtube_trending",
                    "videos": yt_topics[:15],
                })

        elif source == "rss":
            by_cat = {}
            for item in entities:
                if item["entity_type"] != "article_topic":
                    continue
                cat = item.get("category", "General")
                by_cat.setdefault(cat, []).append(item)

            for cat, items in by_cat.items():
                top_items = sorted(
                    items,
                    key=lambda x: x.get("metrics", {}).get("feed_count", 1),
                    reverse=True,
                )[:10]
                trends.append({
                    "trend": f"RSS: {cat}",
                    "source": "rss",
                    "articles": [
                        {
                            "title": i.get("raw", {}).get(
                                "title", i["entity_name"]),
                            "feed_count": i.get("metrics", {}).get(
                                "feed_count", 1),
                        }
                        for i in top_items
                    ],
                })

        elif source == "tiktok_creative_center":
            hashtags = []
            for item in entities:
                if item["entity_type"] != "hashtag":
                    continue
                hashtags.append({
                    "name": item["entity_name"],
                    "post_count": item.get("metrics", {}).get(
                        "post_count", 0),
                    "view_count": item.get("metrics", {}).get(
                        "view_count", 0),
                    "rank": item.get("rank"),
                    "is_mock": item.get("metrics", {}).get("is_mock", False),
                })
            if hashtags:
                trends.append({
                    "trend": "TikTok Trending Hashtags",
                    "source": "tiktok_creative_center",
                    "hashtags": hashtags[:15],
                })

    return trends


SYSTEM_PROMPT = """You are a senior marketing strategist specializing in helping small businesses 
capitalize on trending topics -- even when the connection is not obvious.

Your job is NOT to find trends that directly mention the business's products.
Your job is to find ATTENTION CLUSTERS -- things people are currently thinking about, 
feeling, or searching for -- and figure out how the business can insert itself 
into that conversation in a natural, creative, non-forced way.

Think like this:
- Sports matches are trending -> people gather to watch -> they need snacks/desserts -> 
  a food business can launch "match night boxes"
- Bad weather is trending -> people feel cozy and crave comfort food -> 
  a restaurant can push warm dishes with "rainy day" messaging
- A celebrity/drama is trending -> fan culture is active -> 
  a bakery can do minimal fan-themed cakes

This is called TREND HIJACKING -- legitimate, creative, and extremely effective for 
small businesses with fast production cycles.

FRESHNESS DATA: Each trend may include computed freshness metrics (not guesses):
- freshness_score: higher = more actively accelerating right now (range roughly -1 to +1)
- velocity: rate of change in search interest (positive = growing, negative = declining)
- age_hours: how long since this trend was first detected
- is_new: true if this is the first time we've seen it (insufficient history for velocity)

Use this data to inform your lifecycle and timing judgments:
- High positive velocity + low age = genuinely emerging, weight heavily in top_opportunities
- Negative velocity + high age = declining, consider for skip_these
- is_new = true with no velocity = freshly detected, note it but don't over-prioritize
- Do NOT guess lifecycle from the trend name alone -- use the actual numbers provided

For each trend you analyze:
1. First explain WHY people care about it (the emotion/context behind the search)
2. Then find the BRIDGE -- what human behavior or emotion connects this trend to the business
3. Then give SPECIFIC, actionable product + content ideas (not generic advice)
4. Assign timing: "act now" (24-48h window) | "this week" | "plan ahead" | "skip"
5. Flag brand safety issues if any

Be ruthlessly creative and specific. Never say "consider leveraging this trend."
Say exactly what to make, what to caption it, what format to post it in.

DISCLOSURE AWARENESS: For each specific product or content idea, consider whether it would
plausibly need a disclosure if published. This applies mainly to:
(a) content framed as an endorsement, review, or testimonial-style post
(b) content that is substantially AI-generated and intended to look organic/human-made
(c) content tied to any paid partnership, sponsorship, or affiliate relationship

If one of these applies, add a short "disclosure_note" to that specific idea. Ground it only
in the general, well-established principle behind the FTC's Endorsement Guides: material
connections should be disclosed clearly and conspicuously, and content should not be
deceptive about its true nature. Do NOT invent or assert a more specific rule (exact wording,
placement, a "double disclosure" requirement, or anything else you cannot ground in that
general principle) -- phrase the note as general guidance, e.g. "This reads as a review-style
post -- if there's a material connection here, disclose it clearly" rather than citing a
specific regulation or procedure.

Most ideas won't need this. Only add a disclosure_note when one of (a)/(b)/(c) genuinely
applies to that specific idea -- leave it null for ordinary product or content ideas with no
endorsement, sponsorship, or AI-generated-to-look-organic angle. This is general awareness,
not legal advice, and you are not a substitute for a qualified attorney.

Return ONLY valid JSON. No markdown fences."""


def load_freshness_scores(scores_path):
    """Load trend_scores JSON and build a lookup by normalized entity name."""
    if not scores_path or not Path(scores_path).exists():
        return {}
    data = load_json(scores_path)
    lookup = {}
    for item in data.get("scores", []):
        name = item.get("entity_name", "").lower().strip()
        if name:
            lookup[name] = {
                "freshness_score": item.get("score"),
                "velocity": item.get("velocity"),
                "is_new": item.get("is_new", False),
                "age_hours": item.get("age_hours"),
                "num_observations": item.get("num_observations"),
                "source": item.get("source"),
            }
    return lookup


def enrich_trends_with_freshness(trends, freshness_lookup):
    """Attach freshness data to each trend entry where available."""
    for trend in trends:
        name = trend.get("trend", "").lower().strip()
        if name in freshness_lookup:
            trend["freshness"] = freshness_lookup[name]
        else:
            # Check subtopics if present
            subtopics = (trend.get("rising_subtopics", [])
                         + trend.get("top_subtopics", []))
            matched = [
                freshness_lookup[s.lower().strip()]
                for s in subtopics
                if s.lower().strip() in freshness_lookup
            ]
            if matched:
                best = max(matched,
                           key=lambda x: abs(x.get("freshness_score", 0)))
                trend["freshness"] = best
            else:
                trend["freshness"] = None
    return trends


def build_user_prompt(business, trends):
    geo = business.get("geographic_focus", "the target region")
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## Currently Trending (multi-platform data from Google Trends, YouTube, RSS, and TikTok)
{json.dumps(trends, indent=2)}

Analyze every trend. For each one:
- Find the creative bridge to this business (even if indirect)
- Give specific product ideas and ready-to-use content
- Be as specific as the audience, location ({geo}), and brand tone allow

Return this exact JSON structure:
{{
  "business_snapshot": "one sharp sentence about what makes this business unique",
  "positioning_note": "one insight about where this business should position itself right now",
  "trends": [
    {{
      "trend": "trend name",
      "source": "source",
      "why_people_care": "the emotion/context behind this trend",
      "bridge_to_business": "the creative connection",
      "relevance_score": 0-10,
      "timing": "act now|this week|plan ahead|skip",
      "lifecycle": "emerging|rising|peak|saturating|declining",
      "product_ideas": [
        {{
          "name": "product name",
          "description": "what it is",
          "design_concept": "what it looks like",
          "target": "who buys this",
          "use_case": "when/why they buy it",
          "disclosure_note": "only if relevant, per the DISCLOSURE AWARENESS instructions above -- otherwise null"
        }}
      ],
      "content_ideas": [
        {{
          "platform": "Instagram Reels|Stories|Carousel|WhatsApp|TikTok",
          "hook": "exact opening line or visual",
          "angle": "the narrative angle",
          "caption": "ready-to-post caption with hashtags",
          "disclosure_note": "only if relevant, per the DISCLOSURE AWARENESS instructions above -- otherwise null"
        }}
      ],
      "brand_safety": "safe|caution|avoid",
      "safety_note": null
    }}
  ],
  "top_opportunities": [
    {{
      "rank": 1,
      "trend": "trend name",
      "one_line": "exactly what to do in one sentence",
      "urgency": "today|this week|this month"
    }}
  ],
  "skip_these": ["trend -- reason why it doesn't fit"]
}}"""


def analyze(business, trends):
    # -- DEMO_MODE: skip the real API call for the 3 built-in synthetic
    # businesses, using a hand-written canned analysis instead. See
    # demo_mode.py. Falls through to the real call below for anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business.get("business_name", ""))
        mock = load_mock(preset_key, "trend_analysis.json")
        if mock:
            print(f"  [DEMO_MODE] Using canned trend analysis for "
                  f"'{business.get('business_name')}' -- no API call made.")
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
            "temperature": 0.4,
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
    parser.add_argument("--snapshot", default=None, action="append",
                        help="Path to snapshot_*.json file (repeatable). "
                             "Auto-discovers if not specified.")
    parser.add_argument("--business", default="business_profile.json")
    parser.add_argument("--scores", default=None,
                        help="Path to trend_scores_*.json from trend_scoring.py")
    args = parser.parse_args()

    # Discover snapshot
    snapshot_paths = args.snapshot or []
    if not snapshot_paths:
        snapshot_paths = sorted(
            glob.glob("snapshot_*.json"), reverse=True)

    scores_path = args.scores or find_latest("trend_scores_*.json")

    print("Trend Intelligence Agent")
    print("-" * 40)
    print(f"  Business : {args.business}")
    print(f"  Snapshots: {len(snapshot_paths)} file(s)")
    print(f"  Scores   : {scores_path or '(none)'}\n")

    if not Path(args.business).exists():
        print("  business_profile.json not found.")
        print("   Run: python parse_business.py my_business.txt")
        sys.exit(1)

    business = load_json(args.business)
    trends = load_trends_from_snapshots(snapshot_paths)

    # Enrich with computed freshness scores
    freshness_lookup = load_freshness_scores(scores_path)
    if freshness_lookup:
        trends = enrich_trends_with_freshness(trends, freshness_lookup)
        matched = sum(1 for t in trends if t.get("freshness"))
        print(f"  Freshness: {len(freshness_lookup)} entities scored, "
              f"{matched}/{len(trends)} trends enriched")

    if not trends:
        print("  No trend data found.")
        sys.exit(1)

    print(f"  Loaded {len(trends)} trend groups -> sending to GPT-4o...\n")
    try:
        result = analyze(business, trends)
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

    # Use relative paths in metadata
    result["_meta"] = {
        "generated_at" : datetime.now().isoformat(),
        "trend_count"  : len(trends),
        "business_file": str(Path(args.business).name),
        "snapshot_count": len(snapshot_paths),
        "scores_file"  : str(Path(scores_path).name) if scores_path else None,
    }

    out = f"trend_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"  Analysis saved -> {out}\n")

    opps = result.get("top_opportunities", [])
    if opps:
        print("  Top Opportunities:")
        for o in opps:
            print(f"     {o['rank']}. [{o['urgency'].upper()}] {o['one_line']}")

    print(f"\n  Next: python generate_report.py {out}")


if __name__ == "__main__":
    main()
