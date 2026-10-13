"""Raw logs: every `splunk/raw/*` source. One loader folds physical lines into records with the source's
`break_only_before`, mints the handle, reads the clock through integration.yaml, classes the record against
the served `sampling:` block and writes its template. A small parser per log format pulls out the fields.

Parsers are chosen by the shape of a file's first record, not by a folder or sourcetype name, so the same
code reads the same format wherever it is served. A record its parser cannot read is kept, flagged
`unparsed` and counted in the ingest report; it is never dropped.

Both stack-trace shapes land in `body` the same way (store rule 8): the head line, then the trace lines.
`title` is the line the template is made from: the first physical line of a text record, or the rendered
`LEVEL logger - message` of a JSON one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean, sev_of
from smalls.tools.ingest.masker import mask, template_id


@dataclass
class Parsed:
    """What a format parser reads from one record."""

    title: str
    body: str
    time: str | None = None           # the time as written, in the source's served format
    first_time: str | None = None     # when it first happened, where the record carries both (an aggregated event)
    sev_text: str | None = None
    sev: int | None = None
    status: str | None = None
    native_id: str | None = None
    entity_raw: str | None = None
    session: str | None = None        # the key a sampled session shares (a backend pid)
    attrs: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)


Parser = Callable[[list[str]], "Parsed | None"]

_LOGBACK = re.compile(r"^\S+ \S+ (?P<level>[A-Z]+) +\[(?P<thread>[^\]]+)\] (?P<logger>\S+) - (?P<message>.*)$")
_EXCEPTION = re.compile(r"^(?:Caused by: )?(?P<cls>[\w.$]+(?:Exception|Error))\b")


def logback(lines: list[str]) -> Parsed | None:
    """`%d %-5level [%thread] %logger - %msg%n`, with the stack trace on continuation lines."""
    m = _LOGBACK.match(lines[0])
    if not m:
        return None
    attrs = {"level": m["level"], "thread": m["thread"], "logger": m["logger"]}
    flags = {}
    if len(lines) > 1:
        attrs["lines"] = len(lines)
        exc = next((e["cls"] for e in map(_EXCEPTION.match, lines[1:]) if e), None)
        if exc:
            attrs["exception"] = exc
        # A frame cut by the forwarder's line-length limit: it does not close, and the next line lost its tab.
        if any((ln.startswith("\tat ") and not ln.endswith(")")) or ln.startswith("at ") for ln in lines[1:]):
            flags["truncated"] = True
    return Parsed(title=lines[0], body="\n".join(lines), sev_text=m["level"], sev=sev_of(m["level"]), attrs=attrs, flags=flags)


_POSTGRES = re.compile(r"^\S+ \S+ \S+ \[(?P<pid>\d+)\] (?P<user>[^@ ]*)@(?P<db>\S*) (?P<level>[A-Z]+\d?):  (?P<message>.*)$")


def postgres(lines: list[str]) -> Parsed | None:
    """`log_line_prefix = '%m [%p] %u@%d '`."""
    m = _POSTGRES.match(lines[0])
    if not m:
        return None
    attrs = clean({"level": m["level"], "pid": int(m["pid"]), "user": m["user"], "database": m["db"]})
    return Parsed(title=lines[0], body="\n".join(lines), sev_text=m["level"], sev=sev_of(m["level"]), session=m["pid"], attrs=attrs)


_NGINX = re.compile(r'^(?P<client>\S+) - (?P<user>\S+) (?P<time>\[[^\]]+\]) "(?P<method>[A-Z]+) (?P<uri>\S+) (?P<protocol>HTTP/[\d.]+)" '
                    r'(?P<status>\d{3}) (?P<bytes>\d+) "(?P<referer>[^"]*)" "(?P<user_agent>[^"]*)"(?P<rest>.*)$')
_KV = re.compile(r'(\w+)="?([^"\s]*)"?')


def nginx_access(lines: list[str]) -> Parsed | None:
    """The combined format plus `key=value` timing fields."""
    m = _NGINX.match(lines[0])
    if not m:
        return None
    path, _, query = m["uri"].partition("?")
    status = int(m["status"])
    attrs = clean({"client": m["client"], "method": m["method"], "path": path, "query": query, "status": status,
                   "bytes": int(m["bytes"]), "referer": m["referer"], "user_agent": m["user_agent"], **dict(_KV.findall(m["rest"]))})
    level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
    return Parsed(title=lines[0], body="\n".join(lines), time=m["time"], sev=sev_of(level), status=m["status"], attrs=attrs)


_BIGIP = re.compile(r"^\S+ +\S+ \S+ (?P<device>\S+) (?P<level>\w+) (?P<process>[\w.-]+)\[(?P<pid>\d+)\]: (?P<code>[0-9a-fA-F]{8}:\d): (?P<message>.*)$")
_MEMBER = re.compile(r"Pool (?P<pool>\S+) member (?P<member>\S+)")


def bigip_syslog(lines: list[str]) -> Parsed | None:
    """BIG-IP remote syslog: BSD time with no year, device, level word, process and an `xxxxxxxx:n:` message code."""
    m = _BIGIP.match(lines[0])
    if not m:
        return None
    member = _MEMBER.search(m["message"])
    attrs = clean({"level": m["level"], "device": m["device"], "process": m["process"], "message_code": m["code"],
                   "pool": member and member["pool"], "member": member and member["member"]})
    return Parsed(title=lines[0], body="\n".join(lines), sev_text=m["level"], sev=sev_of(m["level"]),
                  entity_raw=member and member["member"], attrs=attrs)


def logstash_json(lines: list[str]) -> Parsed | None:
    """logstash-logback-encoder JSON, with the shipper's `kubernetes` block; the trace is a `stack_trace` field."""
    try:
        d = json.loads(lines[0])
    except ValueError:
        return None
    if not isinstance(d, dict) or "@timestamp" not in d or "message" not in d:
        return None
    level, logger = d.get("level"), d.get("logger_name")
    trace = (d.get("stack_trace") or "").rstrip("\n")
    skip = {"@timestamp", "@version", "message", "stack_trace", "level_value", "kubernetes", "logger_name", "thread_name"}
    attrs = {"level": level, "thread": d.get("thread_name"), "logger": logger}
    attrs.update({k: v for k, v in d.items() if k not in skip and not isinstance(v, (dict, list))})
    kube = d.get("kubernetes") or {}
    attrs.update({k: kube.get(k) for k in ("namespace", "pod_name", "container_name", "node_name")})
    if trace:
        attrs["lines"] = 1 + trace.count("\n") + 1
        exc = next((e["cls"] for e in map(_EXCEPTION.match, trace.split("\n")) if e), None)
        attrs["exception"] = exc
    return Parsed(title=f"{level} {logger} - {d['message']}", body=d["message"] + ("\n" + trace if trace else ""),
                  time=d["@timestamp"], sev_text=level, sev=sev_of(level), attrs=clean(attrs))


def kube_event(lines: list[str]) -> Parsed | None:
    """A Kubernetes Event object per line. It is one record for all its occurrences: it happened first at
    `firstTimestamp` and is served as it stood at `lastTimestamp`."""
    try:
        d = json.loads(lines[0])
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("kind") != "Event" or "involvedObject" not in d:
        return None
    obj, meta, src = d["involvedObject"], d.get("metadata", {}), d.get("source", {})
    attrs = clean({"type": d.get("type"), "reason": d.get("reason"), "count": d.get("count"), "namespace": obj.get("namespace"),
                   "object_kind": obj.get("kind"), "object_name": obj.get("name"), "component": src.get("component"),
                   "node": src.get("host"), "first_timestamp": d.get("firstTimestamp"), "last_timestamp": d.get("lastTimestamp")})
    return Parsed(title=f"{d.get('type')} {d.get('reason')} {obj.get('kind')}/{obj.get('name')}: {d.get('message')}",
                  body=d.get("message") or "", time=d.get("lastTimestamp"), first_time=d.get("firstTimestamp"),
                  sev_text=d.get("type"), sev=sev_of(d.get("type")), status=d.get("reason"), native_id=meta.get("name"),
                  entity_raw=obj.get("name"), attrs=attrs)


# Tried in order on a file's first record; the first parser that reads it reads the file.
PARSERS: tuple[Parser, ...] = (kube_event, logstash_json, nginx_access, postgres, logback, bigip_syslog)


def fold(lines: list[str], brk: re.Pattern) -> list[tuple[int, list[str]]]:
    """Physical lines into records: a line the break pattern matches starts a record; any other line
    continues the one before. Returns (1-based first line, lines)."""
    records: list[tuple[int, list[str]]] = []
    for n, line in enumerate(lines, 1):
        if brk.search(line) or not records:
            records.append((n, [line]))
        else:
            records[-1][1].append(line)
    return records


class Sampling:
    """The served sampling classes of one source: the first class whose pattern matches a record's first
    line gives its rate. Only the rate and its unit leave this class; names and patterns never do."""

    def __init__(self, block: dict | None):
        self.classes = [(re.compile(c["match"]), int(c["keep_1_in"]), c.get("per")) for c in (block or {}).get("classes", [])]

    def rate(self, first_line: str) -> tuple[int, str | None] | None:
        for pattern, keep_1_in, per in self.classes:
            if pattern.search(first_line):
                return keep_1_in, per
        return None


def raw_logs(bundle, source: str) -> Iterator[Item]:
    integ = bundle.integration
    cfg = integ.sources[source]
    brk = re.compile(cfg["break_only_before"])
    sampling = Sampling(integ.sampling.get(source))
    sourcetype = cfg.get("sourcetype", source)
    for rel in bundle.files(source):
        lines = bundle.text(rel).split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        folded = fold(lines, brk)
        if not folded:
            continue
        parser = next((p for p in PARSERS if p(folded[0][1]) is not None), None)
        host = PurePosixPath(rel).stem if cfg.get("host") == "file_stem" else cfg.get("host")
        seen: dict[str, int] = {}
        out: list[tuple[Record, str | None]] = []
        last_t = 0
        for first_line_no, rec_lines in folded:
            raw = "\n".join(rec_lines)
            seen[raw] = seen.get(raw, 0) + 1
            p = parser(rec_lines) if parser else None
            flags: dict = {}
            if p is None:
                p = Parsed(title=rec_lines[0], body=raw)
                flags["unparsed"] = True
                bundle.report.unparsed.append(f"{rel}:{first_line_no}")
            # A text record's time is what the record-break pattern matched; a parser names it otherwise.
            head = brk.match(rec_lines[0])
            stamp = p.time or (head.group(0).strip() if head else None)
            try:
                t_known, fold_flag = integ.parse(source, stamp)
                t_event = integ.parse(source, p.first_time)[0] if p.first_time else t_known
            except (TypeError, ValueError):
                t_known = t_event = last_t      # keep the record where it sits in the file
                fold_flag = False
                if not flags.get("unparsed"):
                    flags["unparsed"] = True
                    bundle.report.unparsed.append(f"{rel}:{first_line_no}")
            last_t = t_known
            if fold_flag:
                flags["dst_fold"] = True
            flags.update(p.flags)
            template = mask(p.title)
            rec = Record(
                handle=H.k8s_handle(rel, raw) if parser is kube_event else H.log_handle(rel, raw, seen[raw]),
                source=source, dataset="events" if parser is kube_event else "log", kind="log",
                path=rel, locator=first_line_no, native=raw, t_event=t_event, t_known=t_known,
                t_end=t_known if t_event != t_known else None,
                native_id=p.native_id, host=host, entity_raw=p.entity_raw or host,
                t_native=stamp, tz_rule=integ.tz_rule(source), sev=p.sev, sev_text=p.sev_text, status=p.status,
                title=p.title, body=p.body, template_id=template_id(sourcetype, template), template=template,
                attrs=clean({"sourcetype": sourcetype, "index": cfg.get("index"), **p.attrs}), flags=flags)
            rate = sampling.rate(rec_lines[0])
            if rate:
                rec.keep_1_in, rec.sample_per = rate
            out.append((rec, p.session))
        # A line with no class of its own takes the class of its session (a Postgres `connection received`
        # line belongs to the session with the same pid).
        by_session: dict[str, tuple[int, str | None]] = {}
        for rec, session in out:
            if session and rec.keep_1_in is not None:
                by_session.setdefault(session, (rec.keep_1_in, rec.sample_per))
        for rec, session in out:
            if rec.keep_1_in is None and session in by_session:
                rec.keep_1_in, rec.sample_per = by_session[session]
            if rec.keep_1_in is None and sampling.classes:
                bundle.report.unsampled.append(f"{rel}:{rec.locator}")
            yield rec
