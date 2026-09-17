# Backtest report

Seasons: 2024, 2025. Params: k=4.0, a=0.3, b=0.7.

Gate: **FAIL** (model must match or beat consensus on pooled Spearman and MAE).

## Pooled by season

| season | source | spearman | mae | precision | n |
|---|---|---|---|---|---|
| 2024 | consensus | 0.740 | 29.480 | 0.602 | 5642 |
| 2024 | model | 0.696 | 33.684 | 0.615 | 5161 |
| 2025 | consensus | 0.681 | 35.817 | 0.532 | 4750 |
| 2025 | model | 0.655 | 36.379 | 0.530 | 4567 |

## By position

| position | source | spearman | mae | precision | n |
|---|---|---|---|---|---|
| QB | consensus | 0.675 | 49.525 | 0.469 | 1362 |
| QB | model | 0.613 | 55.165 | 0.462 | 1293 |
| RB | consensus | 0.762 | 27.333 | 0.709 | 3282 |
| RB | model | 0.691 | 31.280 | 0.703 | 3131 |
| TE | consensus | 0.638 | 26.323 | 0.450 | 1816 |
| TE | model | 0.632 | 26.625 | 0.473 | 1608 |
| WR | consensus | 0.687 | 34.372 | 0.639 | 3932 |
| WR | model | 0.663 | 34.896 | 0.653 | 3696 |

## By week bucket

| bucket | source | spearman | mae |
|---|---|---|---|
| w10-14 | consensus | 0.703 | 23.737 |
| w10-14 | model | 0.655 | 25.088 |
| w2-5 | consensus | 0.733 | 40.619 |
| w2-5 | model | 0.698 | 45.293 |
| w6-9 | consensus | 0.697 | 35.818 |
| w6-9 | model | 0.679 | 37.199 |
