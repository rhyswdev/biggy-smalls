"""`smalls tag`: the only code that calls the classifier, and the only module that imports its SDK.

It reads the bundle through the adapters, collects every unit whose key is not in the committed cache,
asks the classifier eight at a time with the SDK's own retries, and writes the raw answers back to the
cache. Then it runs the cold repeats over the hand-labelled sample for the stability check. A second run
with nothing new makes no request. The key comes from JEV_API_KEY, is passed to the client explicitly and
is never printed.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from smalls.tools import store
from smalls.tools.ingest.tagging import cache, measure
from smalls.tools.ingest.tagging.profiles import classifier_version, config, profiles
from smalls.tools.ingest.tagging.units import Unit, tag_key

Ask = Callable[[dict, dict], dict]


def sdk_ask() -> Ask:
    """One request to the pinned classifier version: a state and one question in, the raw answer out."""
    key = (os.environ.get("JEV_API_KEY") or "").strip()
    if not key:
        raise SystemExit("smalls tag: JEV_API_KEY is not set. Copy .env.example to .env and fill it in; only `make tag` needs it.")
    try:
        from typesafe_sdk import TypeSafeClient
    except ImportError:
        raise SystemExit("smalls tag: the classifier SDK is not installed. Run it through `make tag` (uv run --group tagging).") from None
    client = TypeSafeClient(api_key=key, model=classifier_version())

    def ask(state: dict, question: dict) -> dict:
        response = client.system_one(state, {"q": question})
        d = response.model_dump(mode="json")
        a = d["answers"]["q"]
        return {"classifier": d["model"], "answer": {"choice": a["choice"], "confidence": a.get("confidence"), "probabilities": a["probabilities"]},
                "usage": {k: (d.get("usage") or {}).get(k) for k in ("input_tokens", "output_tokens")},
                "request_id": getattr(response, "request_id", None)}

    return ask


def _ask_all(jobs: list[tuple], ask: Ask, concurrency: int) -> tuple[list[tuple], list[str]]:
    """Run (label, state, question) jobs, `concurrency` at a time, order kept. Returns the answered jobs
    and one line per failure. A failure never stops the others: a re-run asks only for what is missing."""
    def one(job):
        try:
            return job, ask(job[1], job[2]), None
        except Exception as error:  # the SDK has already retried what can be retried
            return job, None, f"{type(error).__name__}: {str(error)[:200]}"

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(one, jobs))
    return [(job, got) for job, got, err in results if got is not None], [err for _, _, err in results if err]


def fill(units: dict[str, Unit], ask: Ask, path: Path | None = None, concurrency: int = 8) -> dict:
    """Ask the classifier for every unit whose key the cache lacks, and write the cache."""
    known = profiles()
    entries = cache.read(path)
    wanted = {tag_key(u, known[u.facet]): u for u in units.values()}
    jobs = [(key, u.state, known[u.facet].question()) for key, u in sorted(wanted.items()) if key not in entries]
    done, failures = _ask_all(jobs, ask, concurrency)
    for (key, _, _), got in done:
        entries[key] = {"tag_key": key, "profile": known[wanted[key].facet].id, **got}
    if done:
        cache.write(entries, path)
    return {"units": len(wanted), "cached": len(wanted) - len(jobs), "requests": len(done), "failures": failures,
            "input_tokens": sum(g["usage"].get("input_tokens") or 0 for _, g in done),
            "output_tokens": sum(g["usage"].get("output_tokens") or 0 for _, g in done)}


def repeat(units: dict[str, Unit], ask: Ask, n: int, path: Path = measure.REPEATS, labels: Path = measure.LABELS, concurrency: int = 8) -> dict:
    """The cold repeats over the labelled sample: `n` fresh requests per labelled unit, the last with the
    options in a shuffled order. They never touch the cache; they are kept beside the labels."""
    known = profiles()
    have = measure.load_repeats(path)
    version = classifier_version()
    seen = {(r["unit"], r["profile"], r["repeat"]) for r in have if r.get("pinned") == version}
    jobs = []
    for lab in measure.load_labels(labels):
        unit = units.get(lab["unit"])
        if unit is None:
            continue
        profile = known[unit.facet]
        for i in range(1, n + 1):
            if (unit.id, profile.id, i) not in seen:
                shuffled = i == n
                jobs.append(((unit.id, profile.id, i, shuffled), unit.state, profile.question(f"{unit.id}:{i}" if shuffled else None)))
    done, failures = _ask_all(jobs, ask, concurrency)
    for ((unit_id, profile_id, i, shuffled), _, _), got in done:
        have.append({"unit": unit_id, "profile": profile_id, "pinned": version, "repeat": i, "shuffled": shuffled, **got})
    if done:
        have.sort(key=lambda r: (r["unit"], r["profile"], r["repeat"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in have), encoding="utf-8")
    return {"requests": len(done), "failures": failures,
            "input_tokens": sum(g["usage"].get("input_tokens") or 0 for _, g in done),
            "output_tokens": sum(g["usage"].get("output_tokens") or 0 for _, g in done)}


def usage(path: Path | None = None, repeats: Path = measure.REPEATS) -> dict:
    """Every request the committed cache and repeats record, with its tokens and list-price cost."""
    rows = list(cache.read(path).values()) + measure.load_repeats(repeats)
    tokens_in = sum((r.get("usage") or {}).get("input_tokens") or 0 for r in rows)
    tokens_out = sum((r.get("usage") or {}).get("output_tokens") or 0 for r in rows)
    price = float(config()["classifier"]["usd_per_million_input_tokens"])
    return {"requests": len(rows), "input_tokens": tokens_in, "output_tokens": tokens_out, "usd": tokens_in * price / 1_000_000}


def report(bundle_root: Path | str) -> str:
    """The precision, coverage and stability table and the floor it sets. Offline."""
    _, records, _, _ = store.read(bundle_root)
    units = measure.units_of(records)
    rows = measure.outcomes(measure.load_labels(), cache.read(), measure.load_repeats(), units)
    floor, hopeless = measure.final_floor(rows)
    configured = measure.starting_floor()
    lines = [f"labelled units: {len(rows)}; answered: {sum(1 for r in rows if r['answer'])}",
             f"configured floor {configured:.2f}; measured floor {floor if floor is None else format(floor, '.2f')}"
             + (f"; below the bar at any floor (stored, not offered as a filter): {', '.join(hopeless)}" if hopeless else ""),
             measure.render(rows, configured)]
    u = usage()
    lines.append(f"classifier requests on record: {u['requests']} ({u['input_tokens']} input tokens, {u['output_tokens']} output tokens, ${u['usd']:.4f} at list price)")
    return "\n".join(lines)


def main(bundle_root: Path | str, ask: Ask | None = None, repeats: bool = True) -> int:
    _, records, _, _ = store.read(bundle_root)
    units = measure.units_of(records)
    run_cfg = config()["run"]
    need = len(units) - len({tag_key(u) for u in units.values()} & set(cache.read()))
    print(f"smalls tag: {len(units)} units in the bundle, {need} not in the cache")
    ask = ask or sdk_ask()
    filled = fill(units, ask, concurrency=int(run_cfg["concurrency"]))
    print(f"  cache: {filled['requests']} requests, {filled['input_tokens']} input tokens, {filled['output_tokens']} output tokens; {filled['cached']} already cached")
    failures = list(filled["failures"])
    if repeats and measure.load_labels():
        repeated = repeat(units, ask, int(run_cfg["repeats"]), concurrency=int(run_cfg["concurrency"]))
        print(f"  repeats over the labelled sample: {repeated['requests']} requests, {repeated['input_tokens']} input tokens, {repeated['output_tokens']} output tokens")
        failures += repeated["failures"]
    for line in failures[:10]:
        print(f"  ! request failed: {line}")
    print(report(bundle_root))
    if failures:
        print(f"smalls tag: {len(failures)} requests failed; run it again to ask only for those")
    return 1 if failures else 0
