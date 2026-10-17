# Evals

Measure whether a prompt or model change actually makes ScoutAI's output better, instead of guessing.

Both scripts need OpenAI credit (they call the real models) and read `.env` from the project root.

## Copy and campaigns: `copy_eval.py`

Runs the current `campaign_ideas.py` + `ad_copy.py` on the three fixed cases in `cases.json` (a Karachi bakery, a Dubai skincare brand, a Lahore B2B app), then compares against an earlier run with a blind judge.

```bash
python evals/copy_eval.py --dry-run                                   # check setup, no API calls
python evals/copy_eval.py --save my-change --compare baseline-v1-gpt4o
python evals/copy_eval.py --compare my-change --against other-change  # judge two saved runs
python evals/copy_eval.py --save try-4o --model gpt-4o                # try another model
```

- `runs/baseline-v1-gpt4o.json` holds the original prompts' output (before the copy playbook and editor pass), kept so every future version can be compared with where we started.
- The judge (default `gpt-5.5`) compares the two versions in both orders. A version only wins a criterion if it wins both ways.
- Limits, stock phrases and invented offers are checked by code, not the judge.

## Matching: `match_eval.py`

Runs the matcher on a fixed, real trend list (`fixtures/trend_scores_pk.json`, a Pakistan fetch from 2026-10-17) for three example businesses.

```bash
python evals/match_eval.py --dry-run
python evals/match_eval.py --save my-change --judge
```

Reports how many matches are single Reddit posts or global items, the matcher's own relevance scores, and (with `--judge`) a 1-5 rating for reach and fit per match.

## Read the outputs, not only the scores

A judge is a proxy. In the first evaluation it missed two real problems that reading caught: an example in the prompt was leaking into the copy, and the campaign step was inventing a free trial. Look at the saved JSON before trusting a number.

## Results so far

| Date | Change | Result |
|---|---|---|
| 2026-10-17 | Copy playbook + editor pass, on gpt-4o | Copy "publishable" ~5.0 → ~6.4 / 10; won 9 of 12 formats |
| 2026-10-17 | Same, on gpt-5.5 | Copy ~4 → ~8.5–9 / 10; won every format compared; ad copy takes ~60–85s for 4 formats |
| 2026-10-18 | Matching guard rails (no global Reddit, max 1 Reddit match) | Reddit posts in the matching pool: 14.5 → 2.5 per run. Live match quality not yet measured (API credit ran out). |
