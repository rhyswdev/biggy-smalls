"""The committed tag cache and the rule that turns a cached answer into a tag.

`tags/cache.jsonl` holds one line per tag key: the classifier's raw answer (a choice with the probability
of every option, or a yes probability per option), the version that answered and the token usage, and no
record text. Ingest only reads it. A cache miss leaves the record untagged and is counted in the ingest
report; nothing here can reach the network or the classifier SDK.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from smalls.tools.ingest.base import Point, Record
from smalls.tools.ingest.tagging.profiles import OTHER, Profile, floor
from smalls.tools.ingest.tagging.units import Unit, code_tags, tag_key, unit_for, unit_for_point

DEFAULT_CACHE = Path(__file__).resolve().parents[4] / "tags" / "cache.jsonl"


def cache_path() -> Path:
    return Path(os.environ.get("SMALLS_TAG_CACHE") or DEFAULT_CACHE)


def read(path: Path | None = None) -> dict[str, dict]:
    path = path or cache_path()
    if not path.is_file():
        return {}
    return {e["tag_key"]: e for e in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if e}


def write(entries: dict[str, dict], path: Path | None = None) -> None:
    """Sorted by key, one entry per line, so a re-run that changes nothing changes no byte."""
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(entries[k], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for k in sorted(entries))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def decide(answer: dict, profile: Profile, at: float | None = None) -> list[str]:
    """Rule T2, applied by code. Pick-one: the chosen option is one of the profile's own, is not `other`,
    and its probability is at or above the floor. Yes/no: every option whose yes is at or above the floor.
    Anything else stores nothing."""
    at = floor() if at is None else at
    if profile.multi:
        yes = answer.get("nouls") or {}
        return [name for name in profile.options if yes.get(name) is not None and yes[name] >= at]
    choice = answer.get("choice")
    if choice not in profile.options or choice == OTHER:
        return []
    p = (answer.get("probabilities") or {}).get(choice)
    return [choice] if p is not None and p >= at else []


def answer_rows(key: str, answer: dict, profile: Profile) -> list[tuple]:
    """The `tag_answer` rows of one answer: every option with its probability. `chosen` is the classifier's
    own answer, whatever the floor: its choice, or a yes more likely than not."""
    if profile.multi:
        return [(key, profile.facet, name, float(p), int(p >= 0.5)) for name, p in (answer.get("nouls") or {}).items()]
    return [(key, profile.facet, name, float(p), int(name == answer.get("choice"))) for name, p in (answer.get("probabilities") or {}).items()]


def as_tag(values: list[str], profile: Profile):
    """How a profile's stored values sit in `tags`: a list for a yes/no profile, the one value for pick-one."""
    return list(values) if profile.multi else values[0]


def apply(records: list[Record], points: list[Point] = (), path: Path | None = None) -> tuple[list[dict], list[dict], list[tuple], dict]:
    """Tag every record and metric point from the cache. Returns each record's and each point's
    `{tag_key, tags}` in order; the `tag_answer` rows; and the counts for the ingest report."""
    entries = read(path)
    answers: dict[str, list[tuple]] = {}
    stats = {"units": 0, "hits": 0, "misses": 0, "applied": 0, "below_floor": 0, "other": 0, "by_code": 0}
    seen: dict[str, list[str] | None] = {}

    def tag(unit: Unit | None, tags: dict) -> dict:
        if unit is None:
            return {"tag_key": None, "tags": tags}
        profile = unit.profile
        key = tag_key(unit, profile)
        if key not in seen:
            stats["units"] += 1
            entry = entries.get(key)
            if entry is None:
                stats["misses"] += 1
                seen[key] = None
            else:
                stats["hits"] += 1
                values = decide(entry["answer"], profile)
                seen[key] = values
                stats["applied" if values else "other" if entry["answer"].get("choice") == OTHER else "below_floor"] += 1
                answers[key] = answer_rows(key, entry["answer"], profile)
        if seen[key]:
            tags[unit.facet] = as_tag(seen[key], profile)
        return {"tag_key": key, "tags": tags}

    per_record = []
    for r in records:
        tags = dict(code_tags(r))
        stats["by_code"] += len(tags)
        per_record.append(tag(unit_for(r), tags))
    per_point = [tag(unit_for_point(p), {}) for p in points]
    rows = [row for key in sorted(answers) for row in answers[key]]
    return per_record, per_point, rows, stats
