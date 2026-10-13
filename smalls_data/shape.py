"""Statistics for lint invariant 15: does a sampled log keep the shape of what the estate wrote?

Two tests, both against the unsampled population the generator records in `_internal/population.json`:

- Constant rate. Kept units per 10-minute bucket, and in total, sit inside a binomial band around
  population / keep_1_in. A class whose rate changes partway through the incident (the defect data fix 1
  removed) falls outside it at once.
- Shape. Spearman between kept and population counts over 14:20 to 15:10 is above 0.8, at the narrowest
  bucket width (1, 2, 5 or 10 minutes) at which a correct constant-rate sampler passes in 95 percent of
  Monte Carlo draws. A class too sparse at its rate for any width gets the constant-rate test only. This
  width rule replaces a literal per-minute test, which a correct sampler cannot pass at the ruled rates
  (agreed on 2026-10-13; docs/DATA.md).
"""

from __future__ import annotations

import math
import random

WIDTHS = (1, 2, 5, 10)
THRESHOLD = 0.8


def ranks(v: list[float]) -> list[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    out = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def spearman(a: list[float], b: list[float]) -> float | None:
    """Spearman's rho with average ranks for ties; None when either side is constant."""
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da and db else None


def buckets(v: list[float], w: int) -> list[float]:
    return [sum(v[i:i + w]) for i in range(0, len(v), w)]


def simulate(pop: list[float], n: int, per_unit: int, r: random.Random) -> list[float]:
    """Kept records per minute from a correct sampler keeping each unit with probability 1/n."""
    out = []
    for p in pop:
        units = int(round(p / per_unit))
        out.append(per_unit * sum(1 for _ in range(units) if r.random() < 1 / n))
    return out


def shape_width(pop: list[float], n: int, per_unit: int, seed: int = 15, draws: int = 200) -> int | None:
    """Narrowest width at which a correct sampler's 5th-percentile Spearman exceeds THRESHOLD."""
    if n == 1:
        return 1
    r = random.Random(seed)
    for w in WIDTHS:
        P = buckets(pop, w)
        if len(set(P)) < 2:
            return None
        rhos = sorted(spearman(buckets(simulate(pop, n, per_unit, r), w), P) or 0.0 for _ in range(draws))
        if rhos[int(0.05 * draws)] > THRESHOLD:
            return w
    return None


def within_band(kept: float, pop: float, n: int, per_unit: int, slack_units: int = 2) -> bool:
    """Kept records consistent with keeping each unit with probability 1/n (about 4 sigma)."""
    ku, pu = kept / per_unit, pop / per_unit
    expect = pu / n
    sd = math.sqrt(max(expect * (1 - 1 / n), 0.0))
    return abs(ku - expect) <= 4 * sd + slack_units
