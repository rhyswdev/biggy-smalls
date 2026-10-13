"""The store: one in-process SQLite database, in memory, rebuilt at every start from a bundle's served
files (ruling D3; the contract is docs/decisions/ingestion-decisions.md section 3).

What lives here: the DDL, the replay clock and the `visible` views over it, the reader of the served
integration.yaml, the path guard every file read goes through, the loader that runs one adapter per
`sources:` key, and the ingest report. Adapters live in smalls/tools/ingest/.

Three rules the rest of the tool layer relies on:

- Nothing is visible until the clock is set: `clock` holds zero or one row, and every view joins it.
- A tool reads `visible`, `visible_point`, `visible_chunk`, `ref` and `tag_answer`, never the base tables.
- `native` is the bytes as served. A tool shows it only through `render_native`, which leaves out the
  fields whose served value is not true at `now` (they are named in `flags.withheld`).

Columns a tool never prints: `valid_to` (it says when the record next changes) and `t_end` at or after
`now` (the tool that shows a record derives its state at `now`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import yaml

from smalls.tools.ingest.base import UTC, Point, Record, Ref, ms_of, withhold

DDL = """
CREATE TABLE record (
  rid          INTEGER PRIMARY KEY,
  handle       TEXT NOT NULL,          -- minted only through smalls/contracts/handles.py
  version      INTEGER NOT NULL DEFAULT 1,
  source       TEXT NOT NULL,          -- the integration.yaml sources: key
  dataset      TEXT NOT NULL,          -- the file-level set inside the source (incident, events, log, ...)
  kind         TEXT NOT NULL,          -- log, alert, alert_def, change, build, commit, incident, note, event, message, doc, status, config, ...
  native_id    TEXT,
  host         TEXT,                   -- from the Splunk host rule; differs from the handle's file-stem host for F5 and k8s
  entity_raw   TEXT,                   -- unresolved; alias resolution arrives with get_topology in step 7
  entity_id    TEXT,
  stream_key   TEXT,                   -- alert stream; Splunk deliveries share their sid
  t_event      INTEGER NOT NULL,       -- epoch ms UTC
  t_end        INTEGER,
  t_known      INTEGER NOT NULL,       -- epoch ms UTC; 0 for the records with no time in their bytes
  valid_from   INTEGER,
  valid_to     INTEGER,
  t_native     TEXT,                   -- the time as the vendor wrote it
  tz_rule      TEXT,                   -- zone and format used, from integration.yaml
  sev          INTEGER, sev_text TEXT, status TEXT,
  title        TEXT, body TEXT, parent TEXT,
  template_id  TEXT,                   -- logs only: h8 of sourcetype and masked first line
  keep_1_in    INTEGER, sample_per TEXT,   -- logs only, from the served sampling: block; the class name is never stored in a showable column
  tag_key      TEXT,                   -- content hash of what the tagger saw; many records share one
  tags         TEXT NOT NULL DEFAULT '{}',   -- JSON {facet: value}, after thresholds; a list of values for a yes/no facet
  attrs        TEXT NOT NULL DEFAULT '{}',   -- JSON, parsed fields that are safe to show
  native       TEXT NOT NULL,          -- the exact served bytes of the record
  path         TEXT NOT NULL, locator TEXT NOT NULL,
  flags        TEXT NOT NULL DEFAULT '{}',   -- JSON: withheld field paths, DST fold, truncated line, ...
  UNIQUE (handle, valid_from, locator)
);
CREATE TABLE point (handle TEXT NOT NULL, series TEXT NOT NULL, t INTEGER NOT NULL, value REAL, interval_s INTEGER NOT NULL,
                    t_known INTEGER NOT NULL, path TEXT NOT NULL, locator TEXT NOT NULL,
                    tag_key TEXT, tags TEXT NOT NULL DEFAULT '{}');   -- a metric series is tagged like a record
CREATE TABLE chunk (rid INTEGER NOT NULL REFERENCES record(rid), seq INTEGER NOT NULL, heading TEXT, text TEXT NOT NULL);
CREATE VIRTUAL TABLE chunk_fts USING fts5(heading, text, content='chunk', tokenize='porter unicode61');
CREATE TABLE tag_answer (tag_key TEXT NOT NULL, facet TEXT NOT NULL, value TEXT NOT NULL, p REAL NOT NULL, chosen INTEGER NOT NULL);
CREATE TABLE ref (source TEXT NOT NULL, name TEXT NOT NULL, native TEXT NOT NULL,   -- files with no manifest rows
                  flags TEXT NOT NULL DEFAULT '{}');
CREATE TABLE clock (now INTEGER NOT NULL);   -- zero or one row; empty means nothing is visible
CREATE TRIGGER clock_one_row BEFORE INSERT ON clock WHEN (SELECT count(*) FROM clock) > 0
BEGIN SELECT RAISE(ABORT, 'the clock holds at most one row'); END;
CREATE VIEW visible AS
  SELECT r.* FROM record r, clock c
  WHERE r.t_known < c.now
    AND (r.valid_from IS NULL OR r.valid_from <= c.now)
    AND (r.valid_to   IS NULL OR c.now < r.valid_to);
CREATE VIEW visible_point AS
  SELECT p.* FROM point p, clock c WHERE p.t_known < c.now;
CREATE VIEW visible_chunk AS
  SELECT ch.rowid AS chunk_id, ch.* FROM chunk ch JOIN visible v ON v.rid = ch.rid;
CREATE INDEX record_handle ON record (handle);
CREATE INDEX record_source ON record (source, dataset, t_event);
CREATE INDEX record_kind ON record (kind, t_event);
CREATE INDEX record_parent ON record (parent);
CREATE INDEX record_template ON record (template_id);
CREATE INDEX point_series ON point (series, t);
CREATE INDEX chunk_rid ON chunk (rid);
"""

_RECORD_COLUMNS = ("handle", "version", "source", "dataset", "kind", "native_id", "host", "entity_raw", "stream_key", "t_event",
                   "t_end", "t_known", "valid_from", "valid_to", "t_native", "tz_rule", "sev", "sev_text", "status", "title",
                   "body", "parent", "template_id", "keep_1_in", "sample_per", "tag_key", "tags", "attrs", "native", "path",
                   "locator", "flags")

# Nothing under these names is ever read, whatever integration.yaml lists: the manifest and answer key,
# the generator's inputs, and the harness's triggers.
FORBIDDEN = ("_internal", "scenario", "triggers")


class PathRefused(Exception):
    """A read the loader will not make."""


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def render_native(native: str, flags: str | dict | None) -> str:
    """A record's native payload as a tool may show it: the served bytes, minus the fields ingestion
    withholds because their served value is not true at `now` (store rule 6)."""
    f = json.loads(flags) if isinstance(flags, str) else (flags or {})
    paths = tuple(f.get("withheld", ()))
    if not paths:
        return native
    return json.dumps(withhold(json.loads(native), paths), ensure_ascii=False, indent=2)


class Integration:
    """The served integration.yaml: each source's clock, record-break rule and Splunk mapping, and the log
    pipeline's sampling rates. Ingestion reads clocks and rates here and nowhere else."""

    def __init__(self, data: dict):
        self.data = data
        self.sources: dict[str, dict] = data.get("sources") or {}
        self.sampling: dict[str, dict] = data.get("sampling") or {}
        self.syslog_year: int | None = data.get("syslog_year")
        self._zones: dict[str, ZoneInfo] = {}

    def zone(self, source: str) -> ZoneInfo:
        if source not in self._zones:
            name = self.sources[source].get("zone")
            self._zones[source] = ZoneInfo(name if name and name != "none" else "UTC")
        return self._zones[source]

    def tz_rule(self, source: str) -> str:
        cfg = self.sources[source]
        return f"{cfg.get('zone')} {cfg.get('format') or cfg.get('format_note')}"

    def local(self, source: str, naive: datetime) -> tuple[int, bool]:
        """A wall-clock time in the source's zone, as (epoch ms, ambiguous). `ambiguous` is true when the
        zone reads that wall time two ways (a daylight-saving fold or gap); the first reading is kept."""
        zone = self.zone(source)
        first, second = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
        return ms_of(first), first.utcoffset() != second.utcoffset()

    def parse(self, source: str, text: str) -> tuple[int, bool]:
        """A time written in the source's served `format`, as (epoch ms, ambiguous)."""
        fmt = self.sources[source]["format"]
        if "%Y" not in fmt and "%y" not in fmt:      # BSD syslog carries no year
            text, fmt = f"{self.syslog_year} {text}", "%Y " + fmt
        dt = datetime.strptime(text, fmt)
        if dt.tzinfo is not None:
            return ms_of(dt), False
        return self.local(source, dt)


@dataclass
class IngestReport:
    """What ingestion did, per source, and everything it could not do cleanly. Printed by `smalls ingest`."""

    bundle: str = ""
    sources: dict[str, dict[str, int]] = field(default_factory=dict)
    unhandled: list[str] = field(default_factory=list)      # sources: keys with no adapter
    refused: list[str] = field(default_factory=list)        # sources: keys the path guard will not read
    missing: list[str] = field(default_factory=list)        # sources: keys with no file in the bundle
    unparsed: list[str] = field(default_factory=list)       # path:line of records a parser could not read
    unsampled: list[str] = field(default_factory=list)      # log records no served sampling class matched
    templates: int = 0
    tags: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def count(self, source: str, what: str, n: int = 1) -> None:
        row = self.sources.setdefault(source, {"files": 0, "records": 0, "points": 0, "chunks": 0, "refs": 0})
        row[what] += n

    def totals(self) -> dict[str, int]:
        keys = ("files", "records", "points", "chunks", "refs")
        return {k: sum(row[k] for row in self.sources.values()) for k in keys}

    def text(self) -> str:
        lines = [f"ingest report: {self.bundle}", f"  {'source':<28}{'files':>6}{'records':>9}{'points':>8}{'chunks':>8}{'refs':>6}"]
        for source, row in self.sources.items():
            lines.append(f"  {source:<28}{row['files']:>6}{row['records']:>9}{row['points']:>8}{row['chunks']:>8}{row['refs']:>6}")
        t = self.totals()
        lines.append(f"  {'total':<28}{t['files']:>6}{t['records']:>9}{t['points']:>8}{t['chunks']:>8}{t['refs']:>6}")
        lines.append(f"  rows (records + points): {t['records'] + t['points']}; log templates: {self.templates}")
        lines.append(f"  unparsed lines: {len(self.unparsed)}; log records with no sampling class: {len(self.unsampled)}")
        for label, items in (("no adapter for", self.unhandled), ("refused", self.refused), ("no files under", self.missing),
                             ("unparsed", self.unparsed[:20]), ("no sampling class", self.unsampled[:20])):
            for item in items:
                lines.append(f"  ! {label} {item}")
        lines.append("  tags: " + ", ".join(f"{k} {v}" for k, v in self.tags.items()))
        lines.append(f"  built in {self.elapsed_ms:.0f} ms")
        return "\n".join(lines)


class Bundle:
    """The served side of a bundle folder. Every file ingestion reads goes through `text`, which admits
    only paths under a key of integration.yaml `sources:` and refuses `_internal/`, `scenario/` and
    `triggers/` outright."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        config = self.root / "integration.yaml"
        if not config.is_file():
            raise PathRefused(f"{root} is not a bundle: it has no integration.yaml")
        self.integration = Integration(yaml.safe_load(config.read_text(encoding="utf-8")))
        self.name = self.integration.data.get("bundle") or self.root.name
        self.report = IngestReport(bundle=self.name)

    def check(self, rel: str) -> Path:
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise PathRefused(f"{rel}: not a path inside the bundle")
        if not any(rel == s or rel.startswith(s + "/") for s in self.integration.sources):
            raise PathRefused(f"{rel}: not under a source listed in integration.yaml")
        full = (self.root / rel).resolve()
        if not full.is_relative_to(self.root):
            raise PathRefused(f"{rel}: resolves outside the bundle")
        # Checked on the path as written and as resolved, so neither a listed key nor a link gets through.
        for part in (*pure.parts, *full.relative_to(self.root).parts):
            if part in FORBIDDEN:
                raise PathRefused(f"{rel}: {part}/ is never served")
        return full

    def text(self, rel: str) -> str:
        return self.check(rel).read_bytes().decode("utf-8")

    def files(self, source: str, pattern: str = "*") -> list[str]:
        """Every file under a `sources:` key that matches `pattern`, as sorted bundle-relative paths."""
        top = self.check(source)
        if top.is_file():
            return [source]
        found = sorted(p.relative_to(self.root).as_posix() for p in top.rglob(pattern) if p.is_file())
        return [rel for rel in found if self.check(rel)]


class Store:
    def __init__(self, conn: sqlite3.Connection, bundle: Bundle):
        self.conn = conn
        self.bundle = bundle
        self.integration = bundle.integration
        self.report = bundle.report

    # -- the replay clock ------------------------------------------------------------------------------

    def set_now(self, now: int | str | datetime) -> int:
        """Set the replay clock. Takes epoch ms, an aware datetime, an ISO time, or `HH:MM[:SS]` UTC on the
        bundle's day (the date its name ends with)."""
        ms = self.resolve_time(now)
        with self.conn:
            self.conn.execute("DELETE FROM clock")
            self.conn.execute("INSERT INTO clock (now) VALUES (?)", (ms,))
        return ms

    def clear_now(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM clock")

    @property
    def now(self) -> int | None:
        row = self.conn.execute("SELECT now FROM clock").fetchone()
        return row[0] if row else None

    def resolve_time(self, value: int | str | datetime) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, datetime):
            return ms_of(value)
        m = re.fullmatch(r"(\d{1,2}):?(\d{2})(?::(\d{2}))?", value)
        if m:
            day = re.search(r"(\d{4})-(\d{2})-(\d{2})$", self.bundle.name)
            if not day:
                raise ValueError(f"{value}: the bundle name carries no date; give a full ISO time")
            base = datetime(*map(int, day.groups()), tzinfo=UTC)
            return ms_of(base + timedelta(hours=int(m[1]), minutes=int(m[2]), seconds=int(m[3] or 0)))
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return ms_of(dt if dt.tzinfo else dt.replace(tzinfo=UTC))

    # -- inspection ------------------------------------------------------------------------------------

    def dump_hash(self) -> str:
        """sha256 over the SQL dump: two ingests of one bundle give the same hash."""
        h = hashlib.sha256()
        for line in self.conn.iterdump():
            h.update(line.encode("utf-8"))
            h.update(b"\n")
        return h.hexdigest()

    def dump(self, path: Path | str) -> None:
        """Write the store to a SQLite file, for inspection only: nothing reads it back."""
        target = Path(path)
        target.unlink(missing_ok=True)
        out = sqlite3.connect(target)
        with out:
            self.conn.backup(out)
        out.close()

    def visible_counts(self) -> dict[str, int]:
        return {name: self.conn.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
                for name, view in (("records", "visible"), ("points", "visible_point"), ("chunks", "visible_chunk"))}


def tags_enabled(value: str | None = None) -> bool:
    """`SMALLS_TAGS=on|off`, default off (ruling O2). A config value, never a model choice."""
    value = (os.environ.get("SMALLS_TAGS", "off") if value is None else value).strip().lower()
    if value not in ("on", "off"):
        raise ValueError(f"SMALLS_TAGS must be on or off, not {value!r}")
    return value == "on"


def read(bundle_root: Path | str) -> tuple[Bundle, list[Record], list[Point], list[Ref]]:
    """Run every adapter over a bundle's served files. No database, no clock, no tags."""
    from smalls.tools.ingest import adapter_for

    bundle = Bundle(bundle_root)
    report = bundle.report
    records: list[Record] = []
    points: list[Point] = []
    refs: list[Ref] = []
    for source in bundle.integration.sources:
        try:
            files = bundle.files(source)
        except PathRefused as refusal:
            report.refused.append(str(refusal))
            continue
        adapter = adapter_for(bundle, source)
        if adapter is None:
            report.unhandled.append(source)
            continue
        if not files:
            report.missing.append(source)
            continue
        report.count(source, "files", len(files))
        for item in adapter(bundle, source):
            if isinstance(item, Record):
                records.append(item)
                report.count(source, "records")
                report.count(source, "chunks", len(item.chunks))
            elif isinstance(item, Point):
                points.append(item)
                report.count(source, "points")
            else:
                refs.append(item)
                report.count(source, "refs")
    report.templates = len({r.template_id for r in records if r.template_id})
    return bundle, records, points, refs


def build(bundle_root: Path | str, tags: bool | None = None) -> Store:
    """Ingest a bundle into a fresh in-memory store. Reads the bundle's served files and, with tags on, the
    committed tag cache: no network, no key, and the clock is left unset. `tags` defaults to SMALLS_TAGS."""
    started = time.perf_counter()
    bundle, records, points, refs = read(bundle_root)
    report = bundle.report
    untagged = {"tag_key": None, "tags": {}}
    tagged, tagged_points, answers = [untagged] * len(records), [untagged] * len(points), []
    if tags_enabled() if tags is None else tags:
        from smalls.tools.ingest.tagging import cache as tag_cache

        tagged, tagged_points, answers, stats = tag_cache.apply(records, points)
        report.tags = {"mode": "on", **stats}
    else:
        report.tags = {"mode": "off"}

    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    with conn:
        for rid, (r, t) in enumerate(zip(records, tagged), 1):
            row = {c: getattr(r, c, None) for c in _RECORD_COLUMNS}
            row.update(tag_key=t["tag_key"], tags=dumps(t["tags"]), attrs=dumps(r.attrs), flags=dumps(r.flags), locator=str(r.locator))
            conn.execute(f"INSERT INTO record (rid, {', '.join(_RECORD_COLUMNS)}) VALUES (?, {', '.join('?' * len(_RECORD_COLUMNS))})",
                         (rid, *(row[c] for c in _RECORD_COLUMNS)))
            conn.executemany("INSERT INTO chunk (rid, seq, heading, text) VALUES (?, ?, ?, ?)",
                             [(rid, seq, heading, text) for seq, (heading, text) in enumerate(r.chunks)])
        conn.executemany("INSERT INTO point (handle, series, t, value, interval_s, t_known, path, locator, tag_key, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(p.handle, p.series, p.t, p.value, p.interval_s, p.t_known, p.path, p.locator, t["tag_key"], dumps(t["tags"]))
                          for p, t in zip(points, tagged_points)])
        conn.executemany("INSERT INTO ref (source, name, native, flags) VALUES (?, ?, ?, ?)",
                         [(x.source, x.name, x.native, dumps(x.flags)) for x in refs])
        conn.executemany("INSERT INTO tag_answer (tag_key, facet, value, p, chosen) VALUES (?, ?, ?, ?, ?)", answers)
        conn.execute("INSERT INTO chunk_fts (chunk_fts) VALUES ('rebuild')")
    report.elapsed_ms = (time.perf_counter() - started) * 1000
    return Store(conn, bundle)
