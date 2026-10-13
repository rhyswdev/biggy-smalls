"""How tag quality is measured (docs/decisions/ingestion-decisions.md section 4.6): the hand-labelled
sample, and the precision, coverage and stability table with the floor it sets. Everything here is offline:
it reads the labels, the committed cache and the committed repeats.

- Precision: of the values stored on labelled units, how many match the hand label. The bar is 9 in 10.
  A pick-one unit has one label; a yes/no unit has every option that applies, and readings we would not
  count as wrong.
- Coverage: how many labelled units got a tag at all. Reported, no bar.
- Stability: a stored value flips when a cold repeat does not reproduce it: the value comes back more
  than the tolerance under the floor, because the repeat was less sure or answered something else. The
  stored value is the cached answer's, since the cache is the only answer ingest ever uses; the last repeat
  lists the options in a shuffled order. The tolerance (config, 0.02) is the classifier's own wobble: it
  rounds to 0.01 and moves by about that between identical requests, so a value at 0.94 that comes back at
  0.92 has not changed. A pick-one flip that changes the value (one tag in the cache, a different tag in a
  repeat) is counted separately.
- The floor is the lowest value at or above the starting floor at which no stored value flips and every
  facet that can reach the bar reaches it.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from smalls.tools.ingest.tagging.cache import decide
from smalls.tools.ingest.tagging.profiles import OTHER, facets, tolerance
from smalls.tools.ingest.tagging.units import Unit, tag_key, unit_for, unit_for_point

ROOT = Path(__file__).resolve().parents[4]
LABELS = ROOT / "eval" / "tags" / "labels.yaml"
REPEATS = ROOT / "eval" / "tags" / "repeats.jsonl"
BAR = 0.9
START = 0.8

# Which units of each kind the sample takes, as (skip, take) in unit-id order. A unit id is a hash of the
# text, so the sample is fixed before any label or answer exists. The two message kinds skip the units
# first labelled for the pick-one form of their profile (eval/tags/labels-act-pick-one.yaml).
SAMPLE = {"log line": (0, 10), "alert definition": (0, 4), "alert": (0, 3), "incident ticket": (0, 4), "metric series": (0, 14),
          "chat message": (14, 24), "work note": (6, 10),
          "change request": (0, 8), "pipeline run": (0, 6), "commit": (0, 6)}


def units_of(records, points=()) -> dict[str, Unit]:
    """Every distinct unit in a bundle's records and metric points, by unit id."""
    out: dict[str, Unit] = {}
    for unit in [unit_for(r) for r in records] + [unit_for_point(p) for p in points]:
        if unit is not None:
            out.setdefault(unit.id, unit)
    return out


def sample(units: dict[str, Unit]) -> list[Unit]:
    out = []
    for kind, (skip, take) in SAMPLE.items():
        stratum = sorted((u for u in units.values() if u.kind == kind), key=lambda u: u.id)
        out += stratum[skip:skip + take]
    return out


def skeleton(units: dict[str, Unit]) -> str:
    """The sample as YAML with empty labels, for labelling by hand."""
    rows = []
    for u in sample(units):
        row = {"unit": u.id, "facet": u.facet, "kind": u.kind, "text": u.text}
        row.update({"labels": [], "also_acceptable": []} if u.profile.multi else {"label": ""})
        rows.append(row)
    return yaml.safe_dump({"units": rows}, sort_keys=False, allow_unicode=True, width=10_000)


def load_labels(path: Path = LABELS) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["units"] if path.is_file() else []


def load_repeats(path: Path = REPEATS) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x] if path.is_file() else []


def outcomes(labels: list[dict], entries: dict[str, dict], repeats: list[dict], units: dict[str, Unit]) -> list[dict]:
    """Per labelled unit: what the label says should be stored, what would also be acceptable, the cached
    answer and the repeat answers (under the current profile and classifier versions only)."""
    by_unit: dict[str, list[dict]] = {}
    for rep in repeats:
        by_unit.setdefault(rep["unit"], []).append(rep)
    out = []
    for lab in labels:
        unit = units.get(lab["unit"])
        if unit is None:
            continue
        profile = unit.profile
        entry = entries.get(tag_key(unit, profile))
        reps = [r["answer"] for r in sorted(by_unit.get(unit.id, []), key=lambda r: r["repeat"])
                if r["profile"] == profile.id and r["classifier"] == (entry or {}).get("classifier")]
        want = [v for v in (lab["labels"] if "labels" in lab else [lab["label"]]) if v != OTHER]
        out.append({"unit": unit.id, "facet": unit.facet, "kind": unit.kind, "profile": profile, "want": want,
                    "ok": set(want) | set(lab.get("also_acceptable") or []), "answer": entry["answer"] if entry else None, "repeats": reps})
    return out


def _p(answer: dict, value: str, multi: bool) -> float:
    """The probability an answer gives a value: its yes, or its share of a pick-one distribution."""
    return ((answer.get("nouls") if multi else answer.get("probabilities")) or {}).get(value) or 0.0


def table(rows: list[dict], floor: float, tol: float | None = None) -> dict[str, dict]:
    """Per facet at one floor: labelled and answered units, units tagged, values stored, values that match
    the label, precision, coverage, flips and the confused pairs."""
    tol = tolerance() if tol is None else tol
    out: dict[str, dict] = {}
    for facet in facets():
        mine = [r for r in rows if r["facet"] == facet]
        answered = [r for r in mine if r["answer"] is not None]
        tagged = stored = right = flips = value_flips = 0
        confused: Counter = Counter()
        for r in answered:
            values = decide(r["answer"], r["profile"], floor)
            again = [decide(a, r["profile"], floor) for a in r["repeats"]]
            tagged += bool(values)
            stored += len(values)
            for v in values:
                right += v in r["ok"]
                if v not in r["ok"]:
                    confused[f"{'+'.join(r['want']) or OTHER} tagged {v}"] += 1
                flips += any(_p(a, v, r["profile"].multi) < floor - tol - 1e-9 for a in r["repeats"])
                if not r["profile"].multi:
                    value_flips += any(x and v not in x for x in again)
        out[facet] = {"labelled": len(mine), "answered": len(answered), "applied": tagged, "stored": stored, "correct": right,
                      "precision": right / stored if stored else None, "coverage": tagged / len(mine) if mine else None,
                      "repeats": min((len(r["repeats"]) for r in answered), default=0), "flips": flips, "value_flips": value_flips,
                      "confused": dict(sorted(confused.items()))}
    return out


def _passes(facet_row: dict) -> bool:
    return facet_row["flips"] == 0 and (facet_row["precision"] is None or facet_row["precision"] >= BAR)


def final_floor(rows: list[dict]) -> tuple[float | None, list[str]]:
    """The lowest floor from the starting floor up, in steps of 0.01 (the classifier's own rounding), at
    which no stored value flips and every facet that can reach the bar reaches it. Also returns the facets
    that pass at no floor: they are stored but not offered as a tool filter."""
    steps = [round(START + i / 100, 2) for i in range(int(round((1 - START) * 100)) + 1)]
    tables = {f: table(rows, f) for f in steps}
    hopeless = sorted(facet for facet in facets() if not any(_passes(tables[f][facet]) for f in steps))
    for f in steps:
        if all(_passes(row) for facet, row in tables[f].items() if facet not in hopeless):
            return f, hopeless
    return None, hopeless


def render(rows: list[dict], floor: float) -> str:
    """The table as markdown, as docs/DATA.md records it."""
    t = table(rows, floor)
    lines = ["| Facet | Labelled units | Units tagged | Values stored | Match the label | Precision | Coverage | Values a repeat did not reproduce | Changed value | Confused pairs |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for facet, r in t.items():
        precision = "n/a" if r["precision"] is None else f"{r['correct']} of {r['stored']} ({r['precision']:.0%})"
        coverage = "n/a" if r["coverage"] is None else f"{r['coverage']:.0%}"
        confused = "; ".join(f"{k} ({v})" for k, v in r["confused"].items()) or "none"
        lines.append(f"| `{facet}` | {r['labelled']} | {r['applied']} | {r['stored']} | {r['correct']} | {precision} | {coverage} | "
                     f"{r['flips']} over {r['repeats']} repeats | {r['value_flips']} | {confused} |")
    total = {k: sum(r[k] for r in t.values()) for k in ("labelled", "applied", "stored", "correct", "flips", "value_flips")}
    precision = f"{total['correct']} of {total['stored']} ({total['correct'] / total['stored']:.0%})" if total["stored"] else "n/a"
    coverage = f"{total['applied'] / total['labelled']:.0%}" if total["labelled"] else "n/a"
    lines.append(f"| all | {total['labelled']} | {total['applied']} | {total['stored']} | {total['correct']} | {precision} | {coverage} | {total['flips']} | {total['value_flips']} | |")
    return "\n".join(lines)
