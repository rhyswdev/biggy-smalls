"""How tag quality is measured (docs/decisions/ingestion-decisions.md section 4.6): the hand-labelled
sample, and the precision, coverage and stability table with the floor it sets. Everything here is offline:
it reads the labels, the committed cache and the committed repeats.

- Precision: of the tags applied to labelled units, how many match the hand label. The bar is 9 in 10.
- Coverage: how many labelled units got a tag at all. Reported, no bar.
- Stability: an applied tag flips when a cold repeat does not reproduce it, by answering with another
  value or with too little confidence to apply anything. The applied tag is the cached answer's, since the
  cache is the only answer ingest ever uses; the last repeat lists the options in a shuffled order. A flip
  that changes the value (one tag in the cache, a different tag in a repeat) is counted separately: it is
  the kind a higher floor is meant to remove, where a tag that is merely absent in a repeat is doubt.
- The floor is the lowest value at or above the starting floor at which no applied tag flips and every
  facet that can reach the bar reaches it.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from smalls.tools.ingest.tagging.cache import decide
from smalls.tools.ingest.tagging.profiles import profiles
from smalls.tools.ingest.tagging.units import Unit, tag_key, unit_for

ROOT = Path(__file__).resolve().parents[4]
LABELS = ROOT / "eval" / "tags" / "labels.yaml"
REPEATS = ROOT / "eval" / "tags" / "repeats.jsonl"
BAR = 0.9
START = 0.8

# How many units of each kind the sample takes: about 20 per facet. Within a kind the sample is the first
# units by unit id (a hash of the text), so it is fixed before any label or answer exists.
SAMPLE = {("signal", "log line"): 10, ("signal", "alert definition"): 4, ("signal", "alert"): 3, ("signal", "incident ticket"): 4,
          ("act", "chat message"): 14, ("act", "work note"): 6,
          ("change_kind", "change request"): 8, ("change_kind", "pipeline run"): 6, ("change_kind", "commit"): 6}


def units_of(records) -> dict[str, Unit]:
    """Every distinct unit in a bundle's records, by unit id."""
    out: dict[str, Unit] = {}
    for r in records:
        unit = unit_for(r)
        if unit is not None:
            out.setdefault(unit.id, unit)
    return out


def sample(units: dict[str, Unit]) -> list[Unit]:
    out = []
    for (facet, kind), n in SAMPLE.items():
        stratum = sorted((u for u in units.values() if (u.facet, u.kind) == (facet, kind)), key=lambda u: u.id)
        out += stratum[:n]
    return out


def skeleton(units: dict[str, Unit]) -> str:
    """The sample as YAML with empty labels, for labelling by hand."""
    rows = [{"unit": u.id, "facet": u.facet, "kind": u.kind, "text": u.text, "label": ""} for u in sample(units)]
    return yaml.safe_dump({"units": rows}, sort_keys=False, allow_unicode=True, width=10_000)


def load_labels(path: Path = LABELS) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["units"] if path.is_file() else []


def load_repeats(path: Path = REPEATS) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x] if path.is_file() else []


def outcomes(labels: list[dict], entries: dict[str, dict], repeats: list[dict], units: dict[str, Unit]) -> list[dict]:
    """Per labelled unit: its hand label, the cached answer and the repeat answers (under the current
    profile and classifier versions only)."""
    known = profiles()
    by_unit: dict[str, list[dict]] = {}
    for rep in repeats:
        by_unit.setdefault(rep["unit"], []).append(rep)
    out = []
    for lab in labels:
        unit = units.get(lab["unit"])
        if unit is None:
            continue
        profile = known[unit.facet]
        entry = entries.get(tag_key(unit, profile))
        reps = [r["answer"] for r in sorted(by_unit.get(unit.id, []), key=lambda r: r["repeat"])
                if r["profile"] == profile.id and r["classifier"] == (entry or {}).get("classifier")]
        out.append({"unit": unit.id, "facet": unit.facet, "kind": unit.kind, "label": lab["label"],
                    "answer": entry["answer"] if entry else None, "repeats": reps})
    return out


def table(rows: list[dict], floor: float) -> dict[str, dict]:
    """Per facet at one floor: labelled, answered, applied, correct, precision, coverage, flips, confused pairs."""
    known = profiles()
    out: dict[str, dict] = {}
    for facet, profile in known.items():
        mine = [r for r in rows if r["facet"] == facet]
        answered = [r for r in mine if r["answer"] is not None]
        applied = [(r, decide(r["answer"], profile, floor)) for r in answered]
        applied = [(r, v) for r, v in applied if v]
        correct = [r for r, v in applied if v == r["label"]]
        flips = value_flips = 0
        for r, v in applied:
            again = [decide(a, profile, floor) for a in r["repeats"]]
            flips += any(x != v for x in again)
            value_flips += any(x not in (None, v) for x in again)
        confused = Counter(f"{r['label']} tagged {v}" for r, v in applied if v != r["label"])
        out[facet] = {"labelled": len(mine), "answered": len(answered), "applied": len(applied), "correct": len(correct),
                      "precision": len(correct) / len(applied) if applied else None,
                      "coverage": len(applied) / len(mine) if mine else None,
                      "repeats": min((len(r["repeats"]) for r in answered), default=0), "flips": flips, "value_flips": value_flips,
                      "confused": dict(sorted(confused.items()))}
    return out


def _passes(facet_row: dict) -> bool:
    return facet_row["flips"] == 0 and (facet_row["precision"] is None or facet_row["precision"] >= BAR)


def final_floor(rows: list[dict]) -> tuple[float | None, list[str]]:
    """The lowest floor from the starting floor up, in steps of 0.01 (the classifier's own rounding), at
    which no applied tag flips and every facet that can reach the bar reaches it. Also returns the facets
    that reach the bar at no floor: they are stored but not offered as a tool filter."""
    steps = [round(START + i / 100, 2) for i in range(int(round((1 - START) * 100)) + 1)]
    tables = {f: table(rows, f) for f in steps}
    hopeless = sorted(facet for facet in profiles() if not any(_passes(tables[f][facet]) for f in steps))
    for f in steps:
        if all(_passes(row) for facet, row in tables[f].items() if facet not in hopeless):
            return f, hopeless
    return None, hopeless


def render(rows: list[dict], floor: float) -> str:
    """The table as markdown, as docs/DATA.md records it."""
    t = table(rows, floor)
    lines = ["| Facet | Labelled | Tagged | Match the label | Precision | Coverage | Tags a repeat did not reproduce | Of those, changed value | Confused pairs |",
             "|---|---|---|---|---|---|---|---|---|"]
    for facet, r in t.items():
        precision = "n/a" if r["precision"] is None else f"{r['correct']} of {r['applied']} ({r['precision']:.0%})"
        confused = "; ".join(f"{k} ({v})" for k, v in r["confused"].items()) or "none"
        coverage = "n/a" if r["coverage"] is None else f"{r['coverage']:.0%}"
        lines.append(f"| `{facet}` | {r['labelled']} | {r['applied']} | {r['correct']} | {precision} | {coverage} | "
                     f"{r['flips']} over {r['repeats']} repeats | {r['value_flips']} | {confused} |")
    total = {k: sum(r[k] for r in t.values()) for k in ("labelled", "applied", "correct", "flips", "value_flips")}
    precision = f"{total['correct']} of {total['applied']} ({total['correct'] / total['applied']:.0%})" if total["applied"] else "n/a"
    coverage = f"{total['applied'] / total['labelled']:.0%}" if total["labelled"] else "n/a"
    lines.append(f"| all | {total['labelled']} | {total['applied']} | {total['correct']} | {precision} | {coverage} | {total['flips']} | {total['value_flips']} | |")
    return "\n".join(lines)
