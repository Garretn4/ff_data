# Decomposition: why does the model lose to consensus, 2024-2025?

Params: BlendParams(k=4.0, a=0.3, b=0.7). Seasons: [2024, 2025]. Weeks: 2-14. Real cache db: `/Users/garretnelson/code/ff-analyst/cache/stats.duckdb` (read-only). League settings from `/Users/garretnelson/code/ff-analyst/cache/app.sqlite` (read-only).

## Overall
- n = 9728
- model MAE = 35.7045, market MAE = 34.2523, gap = 1.4523
- total sum-of-abs-error gap = 14127.60

## Bucket by outcome (A/B/C)
| bucket | n | model_mae | market_mae | gap_sum | share_of_gap |
|---|---|---|---|---|---|
| A: actual==0 | 820 | 44.899 | 26.938 | 14728.12 | 1.043 |
| B: actual<25% market | 978 | 64.739 | 61.309 | 3354.09 | 0.237 |
| C: rest | 7930 | 31.173 | 31.672 | -3954.61 | -0.280 |

## Bucket by n (games played before the week)
| bucket | n | model_mae | market_mae | gap_sum | share_of_gap |
|---|---|---|---|---|---|
| n=0 | 1215 | 41.176 | 24.360 | 20432.06 | 1.446 |
| n=1-3 | 2862 | 42.019 | 42.830 | -2319.92 | -0.164 |
| n=4-8 | 3863 | 35.200 | 36.014 | -3145.65 | -0.223 |
| n=9+ | 1788 | 22.969 | 23.438 | -838.88 | -0.059 |

## Bucket by position
| position | n | model_mae | market_mae | gap_sum | share_of_gap |
|---|---|---|---|---|---|
| QB | 1293 | 56.067 | 51.011 | 6536.86 | 0.463 |
| RB | 3131 | 31.768 | 28.630 | 9826.23 | 0.696 |
| TE | 1608 | 27.265 | 27.659 | -633.46 | -0.045 |
| WR | 3696 | 35.588 | 36.021 | -1602.03 | -0.113 |

## Signed bias (mean pred - actual)
- overall: model +17.3455, market +13.8769
- bucket A (actual==0): model +44.8994, market +26.9154
- bucket C (rest): model +8.6513, market +6.6788

## Spearman on bucket C only
- overall: model 0.6933, market 0.7279
- bucket C only (n=7930): model 0.7808, market 0.7875
- verdict: model still trails market on C -> gap includes the usage/ranking signal itself, not just availability

## Top 15 by (|model_err| - |market_err|), season 2024
| name | position | week | n | model_pred | market_pred | actual | err_diff |
|---|---|---|---|---|---|---|---|
| Rashee Rice | WR | 8 | 4 | 160.9 | 29.6 | 0.0 | 131.3 |
| Rashee Rice | WR | 6 | 4 | 175.5 | 48.2 | 0.0 | 127.3 |
| Isiah Pacheco | RB | 7 | 2 | 189.3 | 68.6 | 25.0 | 120.7 |
| Isiah Pacheco | RB | 8 | 2 | 173.5 | 60.8 | 25.0 | 112.7 |
| Isiah Pacheco | RB | 5 | 2 | 205.1 | 92.8 | 25.0 | 112.3 |
| Isiah Pacheco | RB | 6 | 2 | 189.3 | 82.0 | 25.0 | 107.3 |
| Rashee Rice | WR | 7 | 4 | 175.5 | 71.2 | 0.0 | 104.3 |
| Isiah Pacheco | RB | 9 | 2 | 157.7 | 59.1 | 25.0 | 98.7 |
| Malik Willis | QB | 4 | 3 | 124.4 | 34.1 | 13.4 | 90.2 |
| Raheem Mostert | RB | 7 | 2 | 145.6 | 56.1 | 55.2 | 89.5 |
| Jameis Winston | QB | 7 | 3 | 16.2 | 104.7 | 142.3 | 88.5 |
| Malik Willis | QB | 5 | 3 | 115.5 | 31.7 | 13.4 | 83.8 |
| Jacoby Brissett | QB | 7 | 5 | 106.1 | 26.8 | 8.3 | 79.3 |
| Ashton Dulin | WR | 2 | 1 | 92.3 | -0.2 | 6.8 | 78.5 |
| Isiah Pacheco | RB | 10 | 2 | 142.0 | 64.2 | 25.0 | 77.8 |

## Interpretation: why does the model lose to consensus?

**It is availability, not ranking skill.** Every piece of evidence points the same way:

- Buckets A (`actual==0`, "player never scored again") and B (`actual < 25% of market
  pred`, "mostly lost") are only 18.5% of rows (820 + 978 of 9,728) but together account
  for **127.7%** of the total model-vs-market error gap (104.3% + 23.7%). Bucket C (the
  81.5% of players who kept producing roughly as expected) has a **negative** share
  (-28.0%) — on that bucket the model's total absolute error is actually *lower* than
  consensus's. Excluding buckets A and B, the model would beat the market on MAE.
- The `n` breakdown makes the mechanism explicit: players with **zero games played
  before the week** (`n=0`, rookies/new starters/players about to be a surprise
  start-or-bust) are 12.5% of rows but carry **144.6%** of the total gap on their own.
  Every other `n` bucket (1-3, 4-8, 9+) has a *negative* share — the model is better than
  consensus once there is any track record at all this season.
- By position, QB (+46.3%) and RB (+69.6%) carry all of the excess error (116% combined);
  TE (-4.5%) and WR (-11.3%) are net *wins* for the model. RB in particular is the
  position most exposed to sudden, in-season role loss (committee changes, injury,
  benching), consistent with the outcome-bucket finding.
- Signed bias confirms the model doesn't just miss randomly, it **systematically
  over-projects**, and much more so for players who go on to score nothing: model bias
  is +44.9 in bucket A vs. +26.9 for the market (both over-project a departed player
  somewhat, since a zero-fill floor guarantees a positive miss, but the model overshoots
  by 67% more than consensus does). In bucket C the two are much closer (+8.65 model vs.
  +6.68 market) — a modest, shared optimism bias rather than a model-specific defect.
- The clinching evidence is the bucket-C Spearman comparison: on players who kept
  playing normally, model Spearman (0.7808) is **within 0.007** of consensus (0.7875) —
  essentially tied. The *overall* Spearman gap (0.6933 vs. 0.7279, a 0.035 deficit) is
  five times larger than the bucket-C gap, so almost all of the rank-correlation
  disadvantage also traces back to how each source handles players who stopped playing,
  not to a real difference in ranking ability among players who kept a role.
- The top-15 worst misses (season 2024) are a roll call of exactly this failure mode:
  **Rashee Rice** (suspended/injured mid-season, model still projected 160-175 points
  while he scored 0), **Isiah Pacheco** (injured for most of the back half of the
  season, model kept projecting 140-205 while his true ROS total was a flat 25 points
  across six different "as-of" weeks), **Raheem Mostert** (lost his role), and a cluster
  of backup/committee QBs with only 3-5 games of history (**Malik Willis, Jacoby
  Brissett, Jameis Winston**) plus one low-usage gunner WR (**Ashton Dulin**, n=1) where
  a single fluke game dominates a thin sample. Winston is the one entry running the
  *other* direction (model way under, actual way over) — a backup who got a surprise
  start and had a career day; the market apparently already knew about the start (his
  consensus prediction of 104.7 tracked the actual 142.3 far better than the model's
  16.2), while the model's blend, anchored on his thin in-season history, didn't.

**Root cause in the code:** `app/model/projections.py`'s `Inputs`/`build_features`/
`blend` pipeline (the one `_prepare_weeks` and the tuned `BlendParams` actually use) does
not consume the `injuries` table or any depth-chart/game-status signal at all — the only
lever it has for a player whose role disappears is a falling `n` (games played) shifting
weight back toward the shrinkage prior via `k`, which reacts slowly (k=4 keeps
meaningful prior weight even at low n) and has no mechanism to *zero out* a player who is
OUT/IR/suspended. `app/model/projections.py::weekly_projection` (a separate, unused-here
code path) already joins an `injured` frame with a `status` column and zeroes IR/Out
players — that machinery exists in the codebase but is not wired into the backtested
blend. Consensus rankings, being human-curated week-to-week, already price in exactly
this kind of news, which is why they win specifically on the players who "went away" and
are statistically tied with the model everywhere else.

