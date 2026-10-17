"""
trend_analyzer.py
------------------
Stage 1 of the trend pipeline: "what's trending, and what's trending for me."

Deliberately lightweight -- no product ideas, no content ideas, no ad copy.
That's Stage 2 (campaign_ideas.py) and Stage 3 (ad_copy.py).

Two outputs:
  - top_trends: the top scored entities from trend_scoring.py, taken as-is.
    Pure data, no GPT call -- these are already individually ranked by
    real freshness/velocity, so there's nothing to interpret here.
  - top_for_your_business: a lightweight GPT-4o pass over that same ranked
    pool, judging which ones have a genuine, defensible connection to this
    specific business (even if creative/indirect) and why. Entities with
    no real connection are left out rather than force-fit.

These two lists overlap when a broadly popular trend also happens to be
relevant to the business, and diverge when it doesn't -- both are useful
signal, which is the point of showing them separately.

Reads trend_scores_<timestamp>.json (from trend_scoring.py).

Output: trend_analyzer_<timestamp>.json

Usage:
    python trend_analyzer.py
    python trend_analyzer.py --scores trend_scores_20260908_120000.json
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
# Override with SCOUTAI_MATCH_MODEL in .env to try another model.
MODEL      = os.environ.get("SCOUTAI_MATCH_MODEL", "gpt-4o")

TOP_TRENDS_COUNT = 15   # shown as-is, no interpretation
# How many top-scored entities GPT considers for relevance.
# Kept well above TOP_TRENDS_COUNT on purpose: scoring ranks by freshness,
# and daily news/search feeds publish far more often than topical ones, so
# a narrow pool fills up with sport and headlines before any food, beauty
# or fashion item reaches it -- which is exactly the material most small
# businesses match against. A wider pool costs a fraction of a cent.
RELEVANCE_POOL_SIZE = 45

# Reddit items are single posts by single people. They're useful when they
# reveal a local conversation, but left unchecked the matcher latches onto
# literal matches ("I baked a cake!" for a bakery) that have no reach.
# Posts from global topical subreddits (r/Baking, r/food) never enter the
# pool -- they're shown in the Reddit tab, not matched -- and local posts
# are capped in the pool and in the final matches.
MAX_REDDIT_IN_POOL = 5
MAX_REDDIT_MATCHES = 1


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest(pattern):
    files = sorted(glob.glob(pattern), reverse=True)
    return files[0] if files else None


def build_relevance_pool(all_scores, pool_size):
    """Pick the entities GPT judges relevance against.

    NOT simply the top N by score. Scores rank freshness, and the feeds
    that publish most often (daily news, search trends) monopolise the
    top of that ranking -- so a straight top-N hands a bakery 45 items
    about football and politics and nothing about food, then asks why
    nothing is relevant.

    Instead this round-robins across categories: the freshest Food item,
    the freshest Fashion item, the freshest News item, and so on, then
    the second of each. Every business type gets a fair shot at the
    material it actually matches against, while still taking the
    strongest entities within each category first.
    """
    by_category = {}
    reddit_kept = 0
    for s in all_scores:  # already sorted by score, descending
        if s.get("source") == "reddit":
            if s.get("region") == "global" or reddit_kept >= MAX_REDDIT_IN_POOL:
                continue
            reddit_kept += 1
        by_category.setdefault(s.get("category") or "General", []).append(s)

    pool = []
    depth = 0
    while len(pool) < pool_size:
        added_this_round = False
        for category in sorted(by_category):
            bucket = by_category[category]
            if depth < len(bucket):
                pool.append(bucket[depth])
                added_this_round = True
                if len(pool) >= pool_size:
                    break
        if not added_this_round:
            break  # every bucket exhausted
        depth += 1

    return pool


SYSTEM_PROMPT = """You are a trend-relevance scout for a small business. You will be given a
business profile and a list of trending entities (topics, videos, hashtags, articles) that
are genuinely trending right now, each with a computed freshness/velocity score -- not a
guess.

Many entities include a "context" field carrying the news headline or article summary
behind the spike. USE IT. A bare term like "argentina" or "psl" is ambiguous on its own;
the context tells you whether it is a football match, a political story, or something
else entirely. Never invent a reason a term is trending when context is provided, and be
cautious about bridging a term whose meaning you cannot establish.

SOURCES DIFFER IN REACH -- weigh them accordingly:
- google_trending_now: a search spike; latest_value is the approximate number of searches.
- google_news: a news story; latest_value is how many outlets are covering it.
- wikipedia: a page people are looking up; latest_value is that day's page views.
- youtube_trending: a video on the country's trending chart; latest_value is views.
- rss: a single article from one publication.
- region "global" means the item comes from an international feed, not the business's
  own country -- usable, but local items matter more to a local business.
- reddit: a single post by one person. One post is NOT a trend. Only use a Reddit post
  when it clearly reflects a wider conversation (a local event, a shared frustration,
  a seasonal moment many people are talking about), and then name that conversation as
  the trend -- never present an individual's post ("cake I made for my wife") as one.

Prefer moments with real reach that the business can join creatively -- a big match,
a holiday, the weather, a viral story, a product everyone is searching for -- over
literal keyword matches. A bakery matching a cricket final ("match-night dessert
boxes") is far more valuable than a bakery matching someone's baking photo.

Your only job here is to judge RELEVANCE, not generate marketing ideas. For each trending
entity that has a real, defensible connection to this business -- even if creative or
indirect -- return:
- why_people_care: the emotion/context behind why this is trending (one sentence)
- bridge_to_business: the one-sentence creative connection to this specific business
- relevance_score: 0-10
- timing: "act now" (24-48h window) | "this week" | "plan ahead"
- lifecycle: "emerging|rising|peak|saturating|declining" -- use the provided freshness/
  velocity numbers to judge this, do not guess from the entity name alone

Skip entities with no genuine connection -- do not force a bridge onto something irrelevant.
Return only the ones worth the business owner's attention, ranked by relevance_score
descending.

This stage is intentionally lightweight. Do NOT generate product ideas, content ideas,
captions, or ad copy here -- that happens in a later stage, from trends the owner selects.

Return ONLY valid JSON. No markdown fences."""


def build_user_prompt(business, pool):
    return f"""## Business Profile
{json.dumps(business, indent=2)}

## Currently Trending ({len(pool)} entities, sampled across categories by computed freshness score, from Google Trends, Google News, YouTube, RSS, Reddit and Wikipedia)
{json.dumps(pool, indent=2)}

Judge relevance only -- see system instructions. Return this exact JSON structure:
{{
  "business_snapshot": "one sharp sentence about what makes this business unique",
  "top_for_your_business": [
    {{
      "trend": "entity_name, copied exactly from the input",
      "source": "source, copied exactly from the input",
      "why_people_care": "the emotion/context behind this trend",
      "bridge_to_business": "the creative connection",
      "relevance_score": 0-10,
      "timing": "act now|this week|plan ahead",
      "lifecycle": "emerging|rising|peak|saturating|declining"
    }}
  ]
}}"""


def enforce_match_rules(matches, pool):
    """Deterministic guard rails on the model's matches.

    - Drop any match whose trend isn't in the pool it was given (the model
      must not invent trends).
    - Keep at most MAX_REDDIT_MATCHES Reddit posts, the highest scored.
    """
    in_pool = {str(p.get("entity_name", "")).lower().strip(): p for p in pool}
    kept, reddit = [], 0
    for m in sorted(matches, key=lambda m: -(m.get("relevance_score") or 0)):
        name = str(m.get("trend", "")).lower().strip()
        src = in_pool.get(name)
        if not src:
            continue
        if src.get("source") == "reddit":
            if reddit >= MAX_REDDIT_MATCHES:
                continue
            reddit += 1
        kept.append(m)
    return kept


def analyze(business, pool):
    # -- DEMO_MODE: skip the real API call for the 3 built-in synthetic
    # businesses, using a hand-written canned relevance pass instead. See
    # demo_mode.py. Falls through to the real call below for anything else.
    if is_demo_mode():
        preset_key = resolve_preset_key(business.get("business_name", ""))
        mock = load_mock(preset_key, "trend_analyzer.json")
        if mock:
            print(f"  [DEMO_MODE] Using canned relevance pass for "
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
            **({"temperature": 0.3} if not MODEL.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")) else {}),
            "response_format": {"type": "json_object"},
            "messages"   : [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": build_user_prompt(business, pool)}
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
    parser.add_argument("--scores", default=None,
                        help="Path to trend_scores_*.json from trend_scoring.py "
                             "(default: latest found)")
    parser.add_argument("--business", default="business_profile.json")
    args = parser.parse_args()

    print("Trend Analyzer (Stage 1: what's trending, what's trending for you)")
    print("-" * 40)

    scores_path = args.scores or find_latest("trend_scores_*.json")
    if not scores_path or not Path(scores_path).exists():
        print("  No trend_scores_*.json found.")
        print("  Run: python trend_scoring.py")
        sys.exit(1)

    if not Path(args.business).exists():
        print(f"  {args.business} not found.")
        print("  Run: python parse_business.py my_business.txt")
        sys.exit(1)

    business = load_json(args.business)
    scores_data = load_json(scores_path)
    all_scores = scores_data.get("scores", [])

    if not all_scores:
        print("  No scored entities found.")
        sys.exit(1)

    print(f"  Business : {args.business}")
    print(f"  Scores   : {scores_path} ({len(all_scores)} entities)\n")

    # Stage 1a: top trends overall -- pure data, already ranked, no GPT
    top_trends = [
        {
            "entity_name": s["entity_name"],
            "source": s["source"],
            "category": s.get("category", ""),
            "context": s.get("context", ""),
            "score": s["score"],
            "velocity": s.get("velocity"),
            "age_hours": s.get("age_hours"),
            "is_new": s.get("is_new", False),
        }
        for s in all_scores[:TOP_TRENDS_COUNT]
    ]

    # Stage 1b: relevance pass over a slightly larger pool.
    # `context` carries the news headline / article summary behind each
    # entity, so the model reasons about why something is actually
    # trending instead of guessing from a bare term like "argentina".
    pool = [
        {
            "entity_name": s["entity_name"],
            "source": s["source"],
            "category": s.get("category", ""),
            "context": s.get("context", ""),
            # Reach: searches, outlets covering, views or listing position,
            # depending on source -- the system prompt explains each.
            "latest_value": s.get("latest_value"),
            # "global" marks items from non-local feeds (e.g. r/food).
            "region": s.get("region", ""),
            "score": s["score"],
            "velocity": s.get("velocity"),
            "age_hours": s.get("age_hours"),
            "is_new": s.get("is_new", False),
        }
        for s in build_relevance_pool(all_scores, RELEVANCE_POOL_SIZE)
    ]

    print(f"  Top trends (data-only)   : {len(top_trends)}")
    print(f"  Relevance pool -> GPT-4o : {len(pool)}\n")

    try:
        result = analyze(business, pool)
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

    # Attach what the model doesn't return but the UI needs to show a
    # match as a real, traceable trend: original title, source link, age.
    by_name = {s["entity_name"]: s for s in all_scores}
    for m in result.get("top_for_your_business", []):
        s = by_name.get(str(m.get("trend", "")).lower().strip())
        if s:
            m.setdefault("source", s.get("source"))
            m["display_title"] = s.get("title") or s["entity_name"]
            m["url"] = s.get("url", "")
            m["thumbnail"] = s.get("thumbnail", "")
            m["age_hours"] = s.get("age_hours")
            m["context"] = s.get("context", "")

    before = len(result.get("top_for_your_business", []))
    result["top_for_your_business"] = enforce_match_rules(
        result.get("top_for_your_business", []), pool)
    dropped = before - len(result["top_for_your_business"])
    if dropped:
        print(f"  Guard rails removed {dropped} match(es) "
              f"(extra Reddit posts, or trends not in the pool)")

    result["top_trends"] = top_trends
    result["_meta"] = {
        "generated_at": datetime.now().isoformat(),
        "business_file": str(Path(args.business).name),
        "scores_file": str(Path(scores_path).name),
        "entities_scored": len(all_scores),
    }

    out = f"trend_analyzer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"  Saved -> {out}\n")

    relevant = result.get("top_for_your_business", [])
    if relevant:
        print("  Top for your business:")
        for r in relevant[:5]:
            print(f"     [{r.get('relevance_score')}/10] {r.get('trend')} "
                  f"({r.get('timing')})")

    print(f"\n  Next: pick trends and generate campaign ideas from them.")


if __name__ == "__main__":
    main()
