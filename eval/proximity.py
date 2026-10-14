"""The proximity baseline (PLAN 12's controls; row Z4 of docs/decisions/eval-loop.md section 15).

    python -m eval.proximity [--bundle <folder>] [--now 14:52] [--recent 45]

Ranks the day's changes for each thing that is alerting, the way a change-correlation tool does: no reading of
logs or chat, only when each change started and whether it touched the same configuration item. A change on the
alerting item ranks above one on a neighbour in the CMDB, which ranks above the rest; within a tier the nearest
change to the alert ranks first. A change that starts up to `AFTER_MIN` minutes after the first alert still counts,
as a deploy under way when the alerts begin is the classic suspect. It shows which change such a tool would put in front of the commander, so a
decoy that ranks first here is one the evidence has to argue down.

Anchors are the incidents and alerts that started in the `--recent` minutes before `now`, each resolved to a CMDB
item by its name (a `service:`, `host:` or `site:` prefix dropped, case and punctuation ignored); one that names no
item is listed and skipped. A build joins the change request its `CHANGE_TICKET` names; a build with none has no
item. The ranking is printed for several look-back windows, since the window decides what can rank at all.

It reads only what the store shows at `now`: no answer key, no manifest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "bundles" / "larkspur-checkout-2026-10-13"
WINDOWS_H = (1, 2, 6, 24)
AFTER_MIN = 10
PREFIXES = ("service:", "host:", "site:", "cluster:", "kube_cronjob:")
TIERS = {2: "same item", 1: "CMDB neighbour", 0: "time only"}


def norm(name: str | None) -> str:
    name = (name or "").strip().lower()
    for p in PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
    name = name.split(".")[0] if re.match(r"^[a-z0-9-]+\.[a-z0-9.-]+\.(internal|local)$", name) else name   # a host's FQDN to its short name
    return re.sub(r"[^a-z0-9]", "", name)


def hhmm(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d %H:%M:%S")


def load(bundle: Path, now: str):
    from smalls.tools import store

    s = store.build(bundle, tags=False)
    now_ms = s.set_now(now)
    rows = s.conn.execute("SELECT handle, kind, entity_raw, t_event, title, attrs FROM visible "
                          "WHERE kind IN ('ci', 'rel', 'change', 'build', 'incident', 'alert') ORDER BY t_event").fetchall()
    return now_ms, rows


def build_model(rows):
    cis: dict[str, str] = {}
    neighbours: dict[str, set[str]] = {}
    changes: dict[str, dict] = {}
    builds = []
    anchors: dict[str, dict] = {}
    for handle, kind, entity, t, title, attrs in rows:
        a = json.loads(attrs or "{}")
        if kind == "ci":
            cis[norm(entity)] = entity
        elif kind == "rel":
            p, c = a.get("parent"), a.get("child")
            neighbours.setdefault(p, set()).add(c)
            neighbours.setdefault(c, set()).add(p)
        elif kind == "change":
            changes.setdefault(a.get("number") or handle.split(":", 1)[1], {"handle": handle, "ci": entity, "start": t, "title": title, "builds": []})
        elif kind == "build":
            builds.append((handle, t, ((a.get("parameters") or {}).get("CHANGE_TICKET")), title))
        elif kind in ("incident", "alert") and handle not in anchors:      # a record's first version is when it started
            anchors[handle] = {"handle": handle, "kind": kind, "entity": entity, "start": t, "title": title}
    units = []
    for number, ch in changes.items():
        units.append({**ch, "id": ch["handle"]})
    by_number = {u["handle"].split(":", 1)[1]: u for u in units}
    for handle, t, ticket, title in builds:
        if ticket in by_number:
            by_number[ticket]["builds"].append(handle)
            by_number[ticket]["start"] = min(by_number[ticket]["start"], t)
        else:
            units.append({"handle": handle, "id": handle, "ci": None, "start": t, "title": title, "builds": []})
    return cis, neighbours, units, list(anchors.values())


def gap(ms: int) -> str:
    m = abs(ms) // 60_000
    return f"{m} min {'after' if ms > 0 else 'before'}" if m else f"{abs(ms) // 1000} s {'after' if ms > 0 else 'before'}"


def tier(change_ci: str | None, anchor_ci: str, neighbours: dict[str, set[str]]) -> int:
    if not change_ci:
        return 0
    if change_ci == anchor_ci:
        return 2
    return 1 if change_ci in neighbours.get(anchor_ci, set()) else 0


def rank(units, anchor_ci: str, t0: int, window_h: int, neighbours) -> list[tuple[int, int, dict]]:
    out = []
    for u in units:
        if t0 - window_h * 3_600_000 <= u["start"] <= t0 + AFTER_MIN * 60_000:
            out.append((tier(u["ci"], anchor_ci, neighbours), u["start"] - t0, u))
    return sorted(out, key=lambda x: (-x[0], abs(x[1])))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.proximity", description=__doc__.split("\n\n")[0])
    p.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    p.add_argument("--now", default="14:52")
    p.add_argument("--recent", type=int, default=45, help="anchors are what started this many minutes before now")
    args = p.parse_args(argv)
    now_ms, rows = load(args.bundle, args.now)
    cis, neighbours, units, anchors = build_model(rows)
    recent = [a for a in anchors if now_ms - args.recent * 60_000 <= a["start"] <= now_ms]
    by_ci: dict[str, dict] = {}
    skipped = []
    for a in recent:
        ci = cis.get(norm(a["entity"]))
        if ci is None:
            skipped.append(a)
        elif ci not in by_ci or a["start"] < by_ci[ci]["start"]:
            by_ci[ci] = a
    print(f"Proximity baseline at {args.now}: {len(units)} changes and builds, {len(recent)} incidents and alerts started in the last {args.recent} min, "
          f"{len(by_ci)} alerting CMDB items.")
    print(f"Each item's changes ranked by tier (same item, CMDB neighbour, time only), then by how near the item's first alert (up to {AFTER_MIN} min after it).\n")
    for ci, a in sorted(by_ci.items(), key=lambda kv: kv[1]["start"]):
        print(f"{ci}: first alert {hhmm(a['start'])} ({a['handle']}, {a['title'][:60]})")
        for w in WINDOWS_H:
            ranked = rank(units, ci, a["start"], w, neighbours)
            top = ranked[:3]
            line = "; ".join(f"{i + 1}. {u['id']} [{u['ci'] or 'no item'}, {TIERS[t]}, {gap(d)}]" for i, (t, d, u) in enumerate(top))
            print(f"  look back {w:>2} h: {line or 'no change in the window'}")
        print()
    if skipped:
        print("Started in the window but name no CMDB item, so not ranked: "
              + ", ".join(f"{a['handle']} ({a['entity'] or 'no entity'})" for a in skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
