"""Shared machinery for the generator: scenario loading, clocks, curves, deterministic randomness,
file writers and the manifest. Every renderer takes a `Ctx` and writes into `ctx.out`."""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from smalls.contracts.handles import log_handle
from smalls.contracts.manifest import ManifestRow

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenario"
CONTENT = SCENARIO / "content"
BUNDLES = ROOT / "bundles"
UTC = timezone.utc


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text())


# A variant scenario (scenario/variants/<id>/) may carry a content folder of its own; a file there replaces the
# base file of the same name. Set by load_ctx for the scenario being built; None builds from the base alone.
OVERLAY: Path | None = None


def content_path(rel: str) -> Path:
    """scenario/content/<rel>, or the variant's own copy of it when the scenario being built has one."""
    if rel.startswith("content/"):
        rel = rel[len("content/"):]
    if OVERLAY is not None and (OVERLAY / rel).exists():
        return OVERLAY / rel
    return CONTENT / rel


def content(rel: str):
    """Load scenario/content/<rel>; `rel` may carry a `#key` fragment."""
    path, _, key = rel.partition("#")
    data = load_yaml(content_path(path))
    return data[key] if key else data


# ---------------------------------------------------------------------------------------------
# Time


class Clock:
    def __init__(self, day: str):
        self.day = date.fromisoformat(day)

    def t(self, s: str | datetime) -> datetime:
        """'14:31:07.221' on the bundle day, or a full ISO time with Z, to an aware UTC datetime."""
        if isinstance(s, datetime):
            return s
        s = str(s)
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)
        parts = s.split(":")
        h, m = int(parts[0]), int(parts[1])
        sec, us = 0, 0
        if len(parts) > 2:
            sp = parts[2].split(".")
            sec = int(sp[0])
            if len(sp) > 1:
                us = int(sp[1].ljust(6, "0")[:6])
        return datetime.combine(self.day, time(h, m, sec, us), tzinfo=UTC)


def ms(dt: datetime) -> int:
    return int(round(dt.timestamp() * 1000))


def epoch_s(dt: datetime) -> int:
    return int(dt.timestamp())


def iso_z(dt: datetime, digits: int = 3) -> str:
    dt = dt.astimezone(UTC)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if digits == 0:
        return base + "Z"
    return base + "." + f"{dt.microsecond:06d}"[:digits] + "Z"


def local(dt: datetime, zone: str) -> datetime:
    return dt.astimezone(ZoneInfo(zone))


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


# ---------------------------------------------------------------------------------------------
# Curves: {points: [[t, v], ...]} interpolates linearly; {steps: [...]} holds each value.


class Curve:
    def __init__(self, spec: dict, clock: Clock):
        self.step = "steps" in spec
        pts = spec.get("steps") or spec.get("points")
        self.pts = sorted((clock.t(t), float(v)) for t, v in pts)

    def __call__(self, dt: datetime) -> float:
        pts = self.pts
        if dt <= pts[0][0]:
            return pts[0][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= dt < t1:
                if self.step:
                    return v0
                f = (dt - t0).total_seconds() / (t1 - t0).total_seconds()
                return v0 + f * (v1 - v0)
        return pts[-1][1]


# ---------------------------------------------------------------------------------------------
# Deterministic randomness: one stream per key, seeded from the scenario seed.


def rng(seed: int, *key) -> random.Random:
    h = hashlib.sha256(("|".join(map(str, (seed,) + key))).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def unit(seed: int, *key) -> float:
    """A deterministic uniform value in [0, 1) for a key; used for per-cart sampling decisions."""
    h = hashlib.sha256(("|".join(map(str, (seed,) + key))).encode()).hexdigest()
    return int(h[:15], 16) / 16 ** 15


def hexid(r: random.Random, n: int) -> str:
    return "".join(r.choice("0123456789abcdef") for _ in range(n))


def sys_id(*key) -> str:
    return hashlib.md5("|".join(map(str, key)).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# Logback %logger{25}


def abbreviate_logger(name: str, length: int = 25) -> str:
    if len(name) <= length:
        return name
    parts = name.split(".")
    out = parts[:]
    for i in range(len(parts) - 1):
        out[i] = parts[i][0]
        if len(".".join(out)) <= length:
            break
    return ".".join(out)


# ---------------------------------------------------------------------------------------------
# Records and the manifest


@dataclass
class Rec:
    """One record a renderer produced, before it is placed in a file."""
    t: datetime
    text: str                      # the full record text; multi-line records joined with "\n"
    cls: str
    kind: str = "noise"
    sig_id: str | None = None
    t_known: datetime | None = None
    order: int = 0                 # tie-breaker for records at the same instant


@dataclass
class Ctx:
    scenario: dict
    facts: dict
    out: Path
    clock: Clock
    seed: int
    rows: list[ManifestRow] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    population: dict = field(default_factory=dict)   # class -> {"HH:MM": records the estate wrote, before sampling}
    per_unit: dict = field(default_factory=dict)     # class -> records per sampled unit (request, session, line)
    pop_keys: dict = field(default_factory=dict)
    datadog_events: list = field(default_factory=list)   # rendered monitor events, for the Slack bot posts     # class -> sampling keys seen (carts), for per-cart classes

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def pop(self, cls: str, t: datetime, n: float = 1, key=None) -> None:
        """Count records of `cls` the estate wrote at `t`, kept or not (lint invariant 15's model curve).
        `key` is the sampling unit a request-path record belongs to (its cart): records of one cart are kept
        or dropped together, so the lint sizes its band by distinct keys, not records."""
        if key is not None:
            self.pop_keys.setdefault(cls, set()).add(key)
        d = self.population.setdefault(cls, {})
        k = t.strftime("%H:%M") if t.date() == self.clock.day else t.strftime("%Y-%m-%dT%H:%M")
        d[k] = d.get(k, 0) + n

    def sampling(self, folder: str) -> dict:
        """Sampling classes of a raw log folder, by generator class: {gen_class: class spec}."""
        out = {}
        for c in self.scenario["sampling"][folder]["classes"]:
            for g in c["gen"]:
                out[g] = c
        return out

    def keep_1_in(self, folder: str, gen_class: str) -> int:
        return int(self.sampling(folder)[gen_class]["keep_1_in"])

    def person(self, handle: str) -> dict:
        for p in self.facts["people"]:
            if p["handle"] == handle:
                return p
        for u in self.facts["integration_users"]:
            if u["user_name"] == handle:
                return {"name": u["name"], "handle": handle, "sn_sys_id": u["sn_sys_id"]}
        raise KeyError(handle)

    def rel(self, p: Path) -> str:
        return p.relative_to(self.out).as_posix()

    def row(self, handle: str, path: Path | str, locator, t: datetime, cls: str, kind: str = "noise",
            sig_id: str | None = None, t_known: datetime | None = None,
            valid_from: datetime | None = None, valid_to: datetime | None = None) -> None:
        rel = path if isinstance(path, str) else self.rel(path)
        # A variant may relabel a generated class whose role differs there (the renderers' default kinds are the
        # development incident's); the label goes to the manifest only.
        kind = self.scenario.get("kind_overrides", {}).get(cls, kind)
        self.rows.append(ManifestRow(
            handle=handle, src=rel.split("/")[0], path=rel, locator=locator, t_utc=iso_z(t),
            t_known=iso_z(t_known or t), valid_from=iso_z(valid_from) if valid_from else None,
            valid_to=iso_z(valid_to) if valid_to else None, class_=cls, sig_id=sig_id, kind=kind))

    # -- writers ---------------------------------------------------------------------------

    def write_json(self, rel: str, obj, indent: int = 2) -> Path:
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=indent, ensure_ascii=False) + "\n")
        return p

    def write_text(self, rel: str, text: str) -> Path:
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def write_log(self, rel: str, recs: list[Rec], counter: str, handle_fn=None) -> list[tuple[Rec, int, str]]:
        """Write line-oriented records sorted by time; each record gets a manifest row at the line of
        its first physical line. Returns (rec, line, handle) for callers that need locators."""
        recs = sorted(recs, key=lambda r: (r.t, r.order))
        lines: list[str] = []
        seen: dict[str, int] = {}
        placed = []
        for r in recs:
            loc = len(lines) + 1
            seen[r.text] = seen.get(r.text, 0) + 1
            h = handle_fn(rel, r.text) if handle_fn else log_handle(rel, r.text, seen[r.text])
            self.row(h, rel, loc, r.t, r.cls, r.kind, r.sig_id, r.t_known)
            placed.append((r, loc, h))
            lines.extend(r.text.split("\n"))
        self.write_text(rel, "\n".join(lines) + "\n")
        self.count(counter, len(lines))
        return placed


def every_nth(counter: dict, key, n: int) -> bool:
    """Deterministic 1-in-n sampling: true on the 1st, (n+1)th, ... occurrence of `key`."""
    c = counter.get(key, 0)
    counter[key] = c + 1
    return c % n == 0


def parse_rate(spec) -> int:
    """'1_in_900' -> 900, 'all' -> 1."""
    if spec in ("all", None):
        return 1
    m = re.fullmatch(r"1_in_(\d+)", str(spec))
    if not m:
        raise ValueError(spec)
    return int(m.group(1))


def facts_path(sc: dict, scenario_file: Path) -> Path:
    """The facts a scenario renders from: scenario/facts.yaml, or a variant's own (`meta.facts`, relative to it)."""
    return (scenario_file.parent / sc["meta"]["facts"]) if sc["meta"].get("facts") else SCENARIO / "facts.yaml"


def load_ctx(scenario_file: Path, out_root: Path = BUNDLES) -> Ctx:
    global OVERLAY
    sc = load_yaml(scenario_file)
    facts = load_yaml(facts_path(sc, scenario_file))
    OVERLAY = (scenario_file.parent / sc["meta"]["content"]) if sc["meta"].get("content") else None
    out = out_root / sc["meta"]["bundle"]
    return Ctx(scenario=sc, facts=facts, out=out, clock=Clock(sc["meta"]["date"]), seed=sc["meta"]["seed"])
