"""
evals/copy_eval.py
------------------
Measure campaign + ad copy quality, so prompt and model changes are
judged on evidence instead of vibes.

Generate a run with the CURRENT code (campaign_ideas.py + ad_copy.py) on
the fixed cases in evals/cases.json, save it, and compare it blind against
an earlier run:

    python evals/copy_eval.py --save my-change                      # generate + save
    python evals/copy_eval.py --save my-change --compare baseline-v1-gpt4o
    python evals/copy_eval.py --compare baseline-v1-gpt4o --against my-change   # judge two saved runs
    python evals/copy_eval.py --dry-run                              # check setup, no API calls

Judging: an LLM judge (default gpt-5.5) compares the two versions per
format, in BOTH orders; a side only wins a criterion if it wins both
ways, which cancels the judge's position bias. Objective checks (limits,
stock phrases, invented offers) come from code, not the judge.

Read the outputs too. A judge is a proxy: in the first evaluation it
missed that an example was leaking into the copy, and that the campaign
step had invented a free trial. Both were caught by reading.

Each full run costs API credit: 3 campaigns + 3 copy runs (draft + editor)
+ 2 judge calls per format compared.
"""

import os, sys, json, time, argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = Path(__file__).resolve().parent
RUNS = EVALS / "runs"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
os.environ["DEMO_MODE"] = "0"     # evals always use the real models

import requests
import campaign_ideas
import ad_copy
from ad_formats import resolve
from copy_playbook import EDITOR_CHECKLIST


def load_cases():
    spec = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    cases = []
    for c in spec["cases"]:
        profile = c.get("profile") or json.loads(
            (ROOT / "mock_data" / c["profile_preset"] / "business_profile.json").read_text(encoding="utf-8"))
        cases.append({"name": c["name"], "business": profile, "trend": c["trend"]})
    return cases, resolve(spec["copy_formats"])


def generate_run(name, cases, formats):
    run = {"name": name, "created": datetime.now().isoformat(timespec="seconds"),
           "model": {"campaign": campaign_ideas.MODEL, "copy": ad_copy.MODEL}, "cases": []}
    for case in cases:
        print(f"  {case['name']}: campaign...", end="", flush=True)
        t0 = time.time()
        camp = campaign_ideas.generate(case["business"], [campaign_ideas.normalize_trend(case["trend"])])["campaigns"][0]
        print(f" {time.time()-t0:.0f}s, copy...", end="", flush=True)
        t0 = time.time()
        copy = ad_copy.generate(case["business"], camp, formats)
        print(f" {time.time()-t0:.0f}s", flush=True)
        run["cases"].append({"case": case["name"], "campaign": camp, "copy": copy})
    return run


# ── Judging ─────────────────────────────────────────────────────
JUDGE = "gpt-5.5"

COPY_JUDGE = """You are judging two versions of ad copy for the same small business and platform,
as an experienced creative director would. Judge only what's on the page. Criteria:
""" + "\n".join(f"- {k}: {d}" for k, d in EDITOR_CHECKLIST) + """
Also give each version a "publishable" score 1-10: how likely a demanding creative director would
approve it to run as-is.
Return JSON: {"criteria": {"<criterion>": "A"|"B"|"tie", ...}, "overall": "A"|"B"|"tie",
"publishable": {"A": n, "B": n}, "reason": "one sentence"}"""

CAMPAIGN_JUDGE = """You are judging two campaign concepts for the same small business and the same trend,
as an experienced strategist would. Criteria:
- big_idea: one clear idea, sayable in a sentence
- insight: built on a real human insight, not just the trend's existence
- natural_link: the trend-to-business link feels natural, not forced
- specific: specific to this business (products, place, audience, tone)
- doable: a small team could run it this week without big budget
- honest: assumes no offers, testimonials or facts the business didn't provide
Return JSON: {"criteria": {"<criterion>": "A"|"B"|"tie", ...}, "overall": "A"|"B"|"tie",
"strength": {"A": 1-10, "B": 1-10}, "reason": "one sentence"}"""


def chat(system, user):
    body = {"model": JUDGE, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if not JUDGE.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        body["temperature"] = 0
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                      json=body, timeout=240)
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


def judge_pair(system, context, a_label, a, b_label, b):
    """Judge a vs b in both orders. Returns verdicts as a_label/b_label/'tie'."""
    r1 = chat(system, f"{context}\n\n## Version A\n{json.dumps(a, ensure_ascii=False)}\n\n## Version B\n{json.dumps(b, ensure_ascii=False)}")
    r2 = chat(system, f"{context}\n\n## Version A\n{json.dumps(b, ensure_ascii=False)}\n\n## Version B\n{json.dumps(a, ensure_ascii=False)}")
    first = {"A": a_label, "B": b_label}
    second = {"A": b_label, "B": a_label}
    out = {"criteria": {}}
    for c, v in r1.get("criteria", {}).items():
        x, y = first.get(v, "tie"), second.get(r2.get("criteria", {}).get(c), "tie")
        out["criteria"][c] = x if x == y else "tie"
    x, y = first.get(r1.get("overall"), "tie"), second.get(r2.get("overall"), "tie")
    out["overall"] = x if x == y else "tie"
    key = "publishable" if "publishable" in r1 else "strength"
    s1, s2 = r1.get(key, {}), r2.get(key, {})
    out["score"] = {a_label: (s1.get("A", 0) + s2.get("B", 0)) / 2,
                    b_label: (s1.get("B", 0) + s2.get("A", 0)) / 2}
    out["reasons"] = [r1.get("reason"), r2.get("reason")]
    return out


def compare(old, new, cases, formats):
    by_case = {c["name"]: c for c in cases}
    old_cases = {c["case"]: c for c in old["cases"]}
    report = {"old": old["name"], "new": new["name"], "judge": JUDGE, "cases": []}
    for nc in new["cases"]:
        oc = old_cases.get(nc["case"])
        if not oc:
            continue
        biz = by_case[nc["case"]]["business"]
        trend = by_case[nc["case"]]["trend"]
        print(f"\n=== {nc['case']}")
        ctx = f"## Business\n{json.dumps(biz, ensure_ascii=False)}\n## Trend\n{json.dumps(trend, ensure_ascii=False)}"
        cj = judge_pair(CAMPAIGN_JUDGE, ctx, "old", oc["campaign"], "new", nc["campaign"])
        print(f"  campaign        overall={cj['overall']:<4} strength old {cj['score']['old']} vs new {cj['score']['new']}")
        brief = json.dumps(biz, ensure_ascii=False)
        probs = {"old": ad_copy.find_problems(oc["copy"], brief), "new": ad_copy.find_problems(nc["copy"], brief)}
        fo = {b.get("format"): b for b in oc["copy"].get("formats", [])}
        fn = {b.get("format"): b for b in nc["copy"].get("formats", [])}
        fjs = []
        for f in formats:
            if f["key"] not in fo or f["key"] not in fn:
                continue
            fctx = f"## Business\n{json.dumps(biz, ensure_ascii=False)}\n## Platform\n{f['label']}: {f['guidance']}"
            j = judge_pair(COPY_JUDGE, fctx, "old", fo[f["key"]], "new", fn[f["key"]])
            j["format"] = f["key"]
            fjs.append(j)
            print(f"  {f['key']:<15} overall={j['overall']:<4} publishable old {j['score']['old']} vs new {j['score']['new']}")
        print(f"  objective problems: old {len(probs['old'])}, new {len(probs['new'])}")
        for p in probs["new"]:
            print(f"     new: {p[:140]}")
        report["cases"].append({"case": nc["case"], "campaign": cj, "formats": fjs, "problems": probs})

    from collections import Counter
    camp = Counter(c["campaign"]["overall"] for c in report["cases"])
    fmt = Counter(j["overall"] for c in report["cases"] for j in c["formats"])
    pub = {s: [j["score"][s] for c in report["cases"] for j in c["formats"]] for s in ("old", "new")}
    print(f"\n==== {old['name']}  vs  {new['name']}  (judge {JUDGE})")
    print("Campaigns overall:", dict(camp))
    print("Copy overall:     ", dict(fmt))
    if pub["new"]:
        print(f"Copy publishable avg: old {sum(pub['old'])/len(pub['old']):.1f} -> new {sum(pub['new'])/len(pub['new']):.1f}")
    print("Objective problems: old", sum(len(c["problems"]["old"]) for c in report["cases"]),
          "-> new", sum(len(c["problems"]["new"]) for c in report["cases"]))
    return report


def load_run(name):
    path = RUNS / f"{name}.json"
    if not path.exists():
        sys.exit(f"No saved run '{name}' in {RUNS}. Available: {', '.join(p.stem for p in RUNS.glob('*.json'))}")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    global JUDGE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--save", help="generate a run with the current code and save it under this name")
    ap.add_argument("--compare", help="saved run to compare against (the 'old' side)")
    ap.add_argument("--against", help="judge --compare against this saved run instead of generating")
    ap.add_argument("--model", help="override the campaign + copy model for this run")
    ap.add_argument("--judge", default=JUDGE, help=f"judge model (default {JUDGE})")
    ap.add_argument("--dry-run", action="store_true", help="check cases and saved runs; no API calls")
    args = ap.parse_args()
    JUDGE = args.judge
    if args.model:
        campaign_ideas.MODEL = ad_copy.MODEL = args.model

    cases, formats = load_cases()
    if args.dry_run:
        print(f"{len(cases)} cases: {', '.join(c['name'] for c in cases)}")
        print(f"formats: {', '.join(f['key'] for f in formats)}")
        print(f"models: campaign={campaign_ideas.MODEL} copy={ad_copy.MODEL} judge={JUDGE}")
        print(f"saved runs: {', '.join(p.stem for p in RUNS.glob('*.json')) or 'none'}")
        return
    if not (args.save or args.against):
        ap.error("use --save NAME to generate a run, or --compare A --against B to judge saved runs")

    new = load_run(args.against) if args.against else None
    if args.save:
        print(f"Generating run '{args.save}' (campaign={campaign_ideas.MODEL}, copy={ad_copy.MODEL})")
        new = generate_run(args.save, cases, formats)
        (RUNS / f"{args.save}.json").write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved -> evals/runs/{args.save}.json")
    if args.compare:
        report = compare(load_run(args.compare), new, cases, formats)
        out = RUNS / f"report_{args.compare}_vs_{new['name']}.json"
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
