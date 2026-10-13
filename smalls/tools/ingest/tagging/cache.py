"""The committed tag cache and the rule that turns a cached answer into a tag.

`tags/cache.jsonl` holds one line per tag key: the classifier's raw answer (its choice and the probability
of every option), the version that answered and the token usage, and no record text. Ingest only reads it.
A cache miss leaves the record untagged and is counted in the ingest report; nothing here can reach the
network or the classifier SDK.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from smalls.tools.ingest.base import Record
from smalls.tools.ingest.tagging.profiles import OTHER, Profile, floor, profiles
from smalls.tools.ingest.tagging.units import code_tags, tag_key, unit_for

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


def decide(answer: dict, profile: Profile, at: float | None = None) -> str | None:
    """Rule T2, applied by code: the chosen option is one of the facet's own, is not `other`, and its
    probability is at or above the floor. Anything else stores nothing."""
    choice = answer.get("choice")
    if choice not in profile.options or choice == OTHER:
        return None
    p = (answer.get("probabilities") or {}).get(choice)
    return choice if p is not None and p >= (floor() if at is None else at) else None


def apply(records: list[Record], path: Path | None = None) -> tuple[list[dict], list[tuple], dict]:
    """Tag every record from the cache. Returns, in record order, each record's `{tag_key, tags}`; the
    `tag_answer` rows; and the counts for the ingest report."""
    entries = read(path)
    known = profiles()
    per_record: list[dict] = []
    answers: dict[tuple[str, str], list[tuple]] = {}
    stats = {"units": 0, "hits": 0, "misses": 0, "applied": 0, "below_floor": 0, "other": 0, "by_code": 0}
    seen: dict[str, str | None] = {}
    for r in records:
        tags = dict(code_tags(r))
        stats["by_code"] += len(tags)
        unit = unit_for(r)
        key = None
        if unit is not None:
            profile = known[unit.facet]
            key = tag_key(unit, profile)
            if key not in seen:
                stats["units"] += 1
                entry = entries.get(key)
                if entry is None:
                    stats["misses"] += 1
                    seen[key] = None
                else:
                    stats["hits"] += 1
                    answer = entry["answer"]
                    value = decide(answer, profile)
                    seen[key] = value
                    stats["applied" if value else "other" if answer.get("choice") == OTHER else "below_floor"] += 1
                    answers[(key, unit.facet)] = [(key, unit.facet, option, float(p), int(option == answer.get("choice")))
                                                  for option, p in (answer.get("probabilities") or {}).items()]
            if seen[key]:
                tags[unit.facet] = seen[key]
        per_record.append({"tag_key": key, "tags": tags})
    rows = [row for key in sorted(answers) for row in answers[key]]
    return per_record, rows, stats
