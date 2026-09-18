# Backtest report

Seasons: 2024, 2025. Params: k=4.0, a=0.3, b=0.7.

Gate: **FAIL** (model must match or beat consensus on pooled Spearman and MAE).

## Pooled by season

| season | source | spearman | mae | precision |
|---|---|---|---|---|
| 2024 | consensus | 0.741 | 29.471 | 0.576 |
| 2024 | model | 0.694 | 33.894 | 0.591 |
| 2025 | consensus | 0.682 | 35.812 | 0.511 |
| 2025 | model | 0.655 | 36.400 | 0.506 |

## By position

| position | source | spearman | mae | precision |
|---|---|---|---|---|
| QB | consensus | 0.675 | 49.525 | 0.469 |
| QB | model | 0.612 | 55.020 | 0.462 |
| RB | consensus | 0.762 | 27.335 | 0.664 |
| RB | model | 0.687 | 31.531 | 0.666 |
| TE | consensus | 0.638 | 26.306 | 0.450 |
| TE | model | 0.633 | 26.582 | 0.473 |
| WR | consensus | 0.688 | 34.360 | 0.591 |
| WR | model | 0.662 | 35.048 | 0.594 |

## By week bucket

| bucket | source | spearman | mae |
|---|---|---|---|
| w10-14 | consensus | 0.703 | 23.740 |
| w10-14 | model | 0.653 | 25.167 |
| w2-5 | consensus | 0.734 | 40.625 |
| w2-5 | model | 0.697 | 45.473 |
| w6-9 | consensus | 0.698 | 35.785 |
| w6-9 | model | 0.678 | 37.296 |
