# Backtest report

Seasons: 2024, 2025. Params: k=4.0, a=0.3, b=0.7.

Gate: **PASS** (model must match or beat consensus on pooled Spearman and MAE).

## Pooled by season

| season | source | spearman | mae | precision | n |
|---|---|---|---|---|---|
| 2024 | consensus | 0.740 | 29.480 | 0.602 | 5642 |
| 2024 | model | 0.741 | 30.659 | 0.624 | 5161 |
| 2025 | consensus | 0.681 | 35.817 | 0.532 | 4750 |
| 2025 | model | 0.697 | 33.719 | 0.561 | 4567 |

## By position

| position | source | spearman | mae | precision | n |
|---|---|---|---|---|---|
| QB | consensus | 0.675 | 49.525 | 0.469 | 1362 |
| QB | model | 0.638 | 53.127 | 0.481 | 1293 |
| RB | consensus | 0.762 | 27.333 | 0.709 | 3282 |
| RB | model | 0.778 | 27.000 | 0.728 | 3131 |
| TE | consensus | 0.638 | 26.323 | 0.450 | 1816 |
| TE | model | 0.651 | 25.514 | 0.492 | 1608 |
| WR | consensus | 0.687 | 34.372 | 0.639 | 3932 |
| WR | model | 0.705 | 32.261 | 0.668 | 3696 |

## By week bucket

| bucket | source | spearman | mae |
|---|---|---|---|
| w10-14 | consensus | 0.703 | 23.737 |
| w10-14 | model | 0.709 | 23.104 |
| w2-5 | consensus | 0.733 | 40.619 |
| w2-5 | model | 0.738 | 41.350 |
| w6-9 | consensus | 0.697 | 35.818 |
| w6-9 | model | 0.712 | 34.385 |
