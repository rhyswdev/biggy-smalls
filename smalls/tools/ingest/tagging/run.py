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
from smalls.tools.ingest.tagging.profiles import Profile, classifier_version, config
from smalls.tools.ingest.tagging.profiles import floor as profile_floor
from smalls.tools.ingest.tagging.profiles import tolerance
from smalls.tools.ingest.tagging.units import Unit, tag_key

Ask = Callable[[dict, dict], dict]      # (state, {question id: question}) -> {classifier, answers, usage, request_id}


def _raw(answer: dict) -> dict:
    """The part of an answer worth keeping: a choice with every option's probability, or a yes probability."""
    if answer.get("type") == "noul" or "noul" in answer:
        return {"noul": answer["noul"]}
    return {"choice": answer["choice"], "confidence": answer.get("confidence"), "probabilities": answer["probabilities"]}


def sdk_ask() -> Ask:
    """One request to the pinned classifier version: a state and its questions in, the raw answers out."""
    key = (os.environ.get("JEV_API_KEY") or "").strip()
    if not key:
        raise SystemExit("smalls tag: JEV_API_KEY is not set. Copy .env.example to .env and fill it in; only `make tag` needs it.")
    try:
        from typesafe_sdk import TypeSafeClient
    except ImportError:
        raise SystemExit("smalls tag: the classifier SDK is not installed. Run it through `make tag` (uv run --group tagging).") from None
    client = TypeSafeClient(api_key=key, model=classifier_version())

    def ask(state: dict, questions: dict) -> dict:
        response = client.system_one(state, questions)
        d = response.model_dump(mode="json")
        return {"classifier": d["model"], "answers": {qid: _raw(a) for qid, a in d["answers"].items()},
                "usage": {k: (d.get("usage") or {}).get(k) for k in ("input_tokens", "output_tokens")},
                "request_id": getattr(response, "request_id", None)}

    return ask


def _ask_all(jobs: list[tuple], ask: Ask, concurrency: int) -> tuple[list[tuple], list[str]]:
    """Run (label, state, questions) jobs, `concurrency` at a time, order kept. Returns the answered jobs
    and one line per failure. A failure never stops the others: a re-run asks only for what is missing."""
    def one(job):
        try:
            return job, ask(job[1], job[2]), None
        except Exception as error:  # the SDK has already retried what can be retried
            return job, None, f"{type(error).__name__}: {str(error)[:200]}"

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(one, jobs))
    return [(job, got) for job, got, err in results if got is not None], [err for _, _, err in results if err]


def _one(got: dict, profile: Profile) -> dict:
    """One unit's result as the cache and the repeats store it: the choice with every option's
    probability, or the yes probability of each option."""
    answer = {"nouls": {name: got["answers"][name]["noul"] for name in profile.options}} if profile.multi else got["answers"]["q"]
    return {"classifier": got["classifier"], "answer": answer, "usage": got["usage"], "request_id": got.get("request_id")}


def fill(units: dict[str, Unit], ask: Ask, path: Path | None = None, concurrency: int = 8) -> dict:
    """Ask the classifier for every unit whose key the cache lacks, and write the cache."""
    entries = cache.read(path)
    wanted = {tag_key(u): u for u in units.values()}
    jobs = [(key, u.state, u.profile.questions()) for key, u in sorted(wanted.items()) if key not in entries]
    done, failures = _ask_all(jobs, ask, concurrency)
    for (key, _, _), got in done:
        entries[key] = {"tag_key": key, "profile": wanted[key].profile.id, **_one(got, wanted[key].profile)}
    if done:
        cache.write(entries, path)
    return {"units": len(wanted), "cached": len(wanted) - len(jobs), "requests": len(done), "failures": failures,
            "input_tokens": sum(g["usage"].get("input_tokens") or 0 for _, g in done),
            "output_tokens": sum(g["usage"].get("output_tokens") or 0 for _, g in done)}


def repeat(units: dict[str, Unit], ask: Ask, n: int, path: Path = measure.REPEATS, labels: Path = measure.LABELS, concurrency: int = 8) -> dict:
    """The cold repeats over the labelled sample: `n` fresh requests per labelled unit, the last with the
    options in a shuffled order. They never touch the cache; they are kept beside the labels."""
    have = measure.load_repeats(path)
    version = classifier_version()
    seen = {(r["unit"], r["profile"], r["repeat"]) for r in have if r.get("pinned") == version}
    jobs = []
    for lab in measure.load_labels(labels):
        unit = units.get(lab["unit"])
        if unit is None:
            continue
        profile = unit.profile
        for i in range(1, n + 1):
            if (unit.id, profile.id, i) not in seen:
                shuffled = i == n
                jobs.append(((unit.id, profile, i, shuffled), unit.state, profile.questions(f"{unit.id}:{i}" if shuffled else None)))
    done, failures = _ask_all(jobs, ask, concurrency)
    for ((unit_id, profile, i, shuffled), _, _), got in done:
        have.append({"unit": unit_id, "profile": profile.id, "pinned": version, "repeat": i, "shuffled": shuffled, **_one(got, profile)})
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
    _, records, points, _ = store.read(bundle_root)
    units = measure.units_of(records, points)
    rows = measure.outcomes(measure.load_labels(), cache.read(), measure.load_repeats(), units)
    floor, hopeless = measure.final_floor(rows)
    configured = profile_floor()
    lines = [f"labelled units: {len(rows)}; answered: {sum(1 for r in rows if r['answer'])}",
             f"configured floor {configured:.2f}; measured floor {floor if floor is None else format(floor, '.2f')} "
             f"(search starts at {measure.START:.2f}; a repeat within {tolerance():.2f} under the floor reproduces a value)"
             + (f"; below the bar at any floor (stored, not offered as a filter): {', '.join(hopeless)}" if hopeless else ""),
             f"at the starting floor {measure.START:.2f}:", measure.render(rows, measure.START),
             f"at the configured floor {configured:.2f}:", measure.render(rows, configured)]
    u = usage()
    lines.append(f"classifier requests on record: {u['requests']} ({u['input_tokens']} input tokens, {u['output_tokens']} output tokens, ${u['usd']:.4f} at list price)")
    return "\n".join(lines)


def experiment(bundle_root: Path | str, spec: Path | str, ask: Ask | None = None) -> int:
    """Ask a file of candidate facets of the bundle's units (`smalls tag --experiment`). Writes the answers
    beside the spec; the committed cache and the shipped profiles are not touched."""
    from smalls.tools.ingest.tagging import experiment as exp

    spec = Path(spec)
    _, records, points, _ = store.read(bundle_root)
    units = measure.units_of(records, points)
    candidates, version = exp.load(spec)
    print(f"smalls tag: experiment {spec.name} ({version}), candidates: {', '.join(candidates)}")
    got = exp.run(spec, units, ask or sdk_ask(), _ask_all, concurrency=int(config()["run"]["concurrency"]))
    print(f"  {got['requests']} requests, {got['input_tokens']} input tokens, {got['output_tokens']} output tokens; answers for {got['units']} units")
    for line in got["failures"][:10]:
        print(f"  ! request failed: {line}")
    return 1 if got["failures"] else 0


def main(bundle_root: Path | str, ask: Ask | None = None, repeats: bool = True) -> int:
    _, records, points, _ = store.read(bundle_root)
    units = measure.units_of(records, points)
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
