"""
evals/match_eval.py
-------------------
Measure trend-to-business matching on a fixed, real trend list
(evals/fixtures/trend_scores_pk.json), so changes to the matcher can be
compared run to run.

    python evals/match_eval.py --save my-change            # run + save
    python evals/match_eval.py --save my-change --judge    # also rate each match
    python evals/match_eval.py --dry-run                   # check setup, no API calls

Reports per business:
  - how many matches, and how many are single Reddit posts or global items
    (the failure we fixed: "I baked a cake!" matched to a bakery)
  - average relevance score the matcher gave
  - with --judge: an LLM rates each match 1-5 for REACH (is this a real
    trend many people care about?) and FIT (is the link to the business
    natural?). Treat as a proxy, and read the matches yourself.
"""

import os, sys, json, argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = Path(__file__).resolve().parent
RUNS = EVALS / "runs"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
os.environ["DEMO_MODE"] = "0"

import requests
import trend_analyzer as ta

JUDGE_PROMPT = """You are reviewing trend matches a tool suggested to a small business. For each match,
rate 1-5:
- reach: is this a real trend many people in the market care about right now (5), or a single
  person's post / niche item (1)?
- fit: is the link to this specific business natural and tasteful (5), or forced (1)?
Return JSON: {"ratings": [{"trend": "...", "reach": n, "fit": n}]}"""


def judge(business, matches, model):
    body = {"model": model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": JUDGE_PROMPT},
                         {"role": "user", "content": json.dumps({"business": business, "matches": matches}, ensure_ascii=False)}]}
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, json=body, timeout=240)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"]).get("ratings", [])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="save results under this name")
    ap.add_argument("--model", help="override the matching model")
    ap.add_argument("--judge", action="store_true", help="also rate each match with an LLM judge")
    ap.add_argument("--judge-model", default="gpt-5.5")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.model:
        ta.MODEL = args.model

    scores = json.loads((EVALS / "fixtures" / "trend_scores_pk.json").read_text(encoding="utf-8"))["scores"]
    presets = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))["match_businesses"]
    pool = ta.build_relevance_pool(scores, ta.RELEVANCE_POOL_SIZE)
    reddit_in_pool = sum(1 for p in pool if p["source"] == "reddit")
    print(f"Fixture: {len(scores)} trends -> pool of {len(pool)} ({reddit_in_pool} Reddit posts)")
    if args.dry_run:
        print(f"Businesses: {', '.join(presets)} | model: {ta.MODEL}")
        return

    pool_fields = [{k: p.get(k) for k in ("entity_name", "source", "category", "context", "latest_value",
                                          "region", "score", "velocity", "age_hours", "is_new")} for p in pool]
    run = {"name": args.save or "unsaved", "created": datetime.now().isoformat(timespec="seconds"),
           "model": ta.MODEL, "businesses": []}
    for key in presets:
        biz = json.loads((ROOT / "mock_data" / key / "business_profile.json").read_text(encoding="utf-8"))
        result = ta.analyze(biz, pool_fields)
        matches = ta.enforce_match_rules(result.get("top_for_your_business", []), pool)
        by_name = {p["entity_name"]: p for p in pool}
        rows = [{"trend": m.get("trend"), "source": by_name.get(str(m.get("trend", "")).lower().strip(), {}).get("source"),
                 "region": by_name.get(str(m.get("trend", "")).lower().strip(), {}).get("region"),
                 "relevance": m.get("relevance_score"), "bridge": m.get("bridge_to_business")} for m in matches]
        ratings = judge(biz, rows, args.judge_model) if args.judge and rows else []
        for r in rows:
            hit = next((x for x in ratings if str(x.get("trend", "")).lower() == str(r["trend"]).lower()), {})
            r["reach"], r["fit"] = hit.get("reach"), hit.get("fit")
        n = len(rows)
        reddit = sum(1 for r in rows if r["source"] == "reddit")
        glob_ = sum(1 for r in rows if r["region"] == "global")
        avg = lambda xs: sum(xs) / len(xs) if xs else 0
        summary = {"matches": n, "reddit": reddit, "global": glob_,
                   "avg_relevance": round(avg([r["relevance"] or 0 for r in rows]), 1),
                   "avg_reach": round(avg([r["reach"] for r in rows if r["reach"]]), 1) if ratings else None,
                   "avg_fit": round(avg([r["fit"] for r in rows if r["fit"]]), 1) if ratings else None}
        run["businesses"].append({"business": biz.get("business_name"), "summary": summary, "matches": rows})
        print(f"\n=== {biz.get('business_name')}: {summary}")
        for r in rows:
            rating = f" reach {r['reach']} fit {r['fit']}" if r["reach"] else ""
            print(f"  [{r['relevance']}/10] {r['trend'][:60]}  <{r['source']}>{rating}")
            print(f"      -> {(r['bridge'] or '')[:110]}")

    if args.save:
        out = RUNS / f"match_{args.save}.json"
        out.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
