"""Live game state with player projections over remaining weeks."""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class Projection:
    source: str
    weekly: pl.DataFrame
    ppg: pl.DataFrame
