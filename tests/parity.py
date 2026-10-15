"""The comparison behind the ingestion parity tests: every manifest row against the store row at the same
path and locator. Only tests read `_internal/manifest.jsonl`, and only its handle, path, locator and time
columns: the scenario's labels (`class`, `sig_id`, `kind`) are dropped on load."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The development bundle. The session that authors a held-out bundle points the leak tests at it with
# SMALLS_TEST_BUNDLE and SMALLS_TEST_SCENARIO (eval/heldout/README.md, acceptance 1); nothing else sets them.
BUNDLE = ROOT / os.environ.get("SMALLS_TEST_BUNDLE", "bundles/larkspur-checkout-2026-10-13")
COLUMNS = ("handle", "path", "locator", "t_utc", "t_known", "valid_from", "valid_to")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def ms(iso: str | None) -> int | None:
    if iso is None:
        return None
    delta = datetime.fromisoformat(iso.replace("Z", "+00:00")) - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def manifest(bundle: Path = BUNDLE) -> list[dict]:
    rows = []
    for line in (bundle / "_internal/manifest.jsonl").read_text().splitlines():
        d = json.loads(line)
        rows.append({k: d[k] for k in COLUMNS})
    return rows


def visible_at(row: dict, now: int) -> bool:
    """PLAN 3.4, stated on the manifest: known before now, and inside the version's validity window."""
    if ms(row["t_known"]) >= now:
        return False
    if row["valid_from"] and ms(row["valid_from"]) > now:
        return False
    if row["valid_to"] and now >= ms(row["valid_to"]):
        return False
    return True


def store_rows(conn, visible: bool = False) -> dict[tuple[str, str], list[dict]]:
    """Every store row that stands for a manifest row (records and metric points), by (path, locator)."""
    record, point = ("visible", "visible_point") if visible else ("record", "point")
    out: dict[tuple[str, str], list[dict]] = {}
    for h, path, loc, te, tk, vf, vt in conn.execute(f"SELECT handle, path, locator, t_event, t_known, valid_from, valid_to FROM {record}"):
        out.setdefault((path, loc), []).append(dict(handle=h, t_event=te, t_known=tk, valid_from=vf, valid_to=vt, table="record"))
    for h, path, loc, t, tk in conn.execute(f"SELECT handle, path, locator, t, t_known FROM {point}"):
        out.setdefault((path, loc), []).append(dict(handle=h, t_event=t, t_known=tk, valid_from=None, valid_to=None, table="point"))
    return out


def problems(conn, rows: list[dict]) -> list[str]:
    """Every way the store differs from the manifest. A store row with `t_known = 0` has no time in its
    bytes (store rule 3): its times are not compared, and the caller asserts it is always visible."""
    got = store_rows(conn)
    out = []
    keys = set()
    for r in rows:
        key = (r["path"], str(r["locator"]))
        keys.add(key)
        hits = got.get(key, [])
        if len(hits) != 1:
            out.append(f"{r['handle']} at {key}: {len(hits)} store rows")
            continue
        s = hits[0]
        if s["handle"] != r["handle"]:
            out.append(f"{key}: handle {s['handle']} is not {r['handle']}")
        if s["t_known"] == 0:
            continue
        for col, want in (("t_event", r["t_utc"]), ("t_known", r["t_known"]), ("valid_from", r["valid_from"]), ("valid_to", r["valid_to"])):
            if s[col] != ms(want):
                out.append(f"{r['handle']} at {key}: {col} {s[col]} is not {want} ({ms(want)})")
    out += [f"store row at {key} has no manifest row" for key in got if key not in keys]
    return out
