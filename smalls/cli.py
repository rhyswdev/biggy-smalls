"""The `smalls` command.

    smalls ingest --bundle <folder> [--dump x.db] [--now HH:MM]   build the store, print the ingest report
    smalls tag    --bundle <folder> [--report | --sample]         compute tag answers into the committed cache
    smalls run    --now <time> --trigger <text> [--bundle <folder>]   investigate (python -m smalls.agent run)
    smalls resume <run_id>                                        resume a run paused at a gate
    smalls replay <run_id|folder>                                 re-render a run record; no model, no key

    smalls tools call <name> --now <time> key=value ...           call one tool; print its text, then its handles
    smalls tools list | schema [<name>]                           the tool surface

`ingest` reads only the bundle and the committed tag cache: no key, no network. `tag` is the only command
that calls the classifier and the only one that needs JEV_API_KEY.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bundle(arg: str | None) -> Path:
    if not arg:
        raise SystemExit("smalls: name a bundle with --bundle <folder> or SMALLS_BUNDLE")
    path = Path(arg)
    if not (path / "integration.yaml").is_file():
        raise SystemExit(f"smalls: {path} is not a bundle (no integration.yaml)")
    return path


def cmd_ingest(args: argparse.Namespace) -> int:
    from smalls.tools import store

    built = store.build(_bundle(args.bundle))
    print(built.report.text())
    if args.now:
        now = built.set_now(args.now)
        counts = built.visible_counts()
        print(f"  visible at {args.now} ({now} ms): " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    print(f"  dump sha256: {built.dump_hash()}")
    if args.dump:
        built.dump(args.dump)
        print(f"  wrote {args.dump}")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    bundle = _bundle(args.bundle)
    if args.sample:
        from smalls.tools import store
        from smalls.tools.ingest.tagging import measure

        _, records, points, _ = store.read(bundle)
        print(measure.skeleton(measure.units_of(records, points)), end="")
        return 0
    from smalls.tools.ingest.tagging import run

    if args.report:
        print(run.report(bundle))
        return 0
    if args.experiment:
        return run.experiment(bundle, args.experiment)
    return run.main(bundle, repeats=not args.no_repeats)


def tool_arguments(spec, pairs: list[str]) -> dict:
    """`key=value` pairs as a tool's arguments. A value is read as JSON when it parses as JSON, so a list
    or a list of name and value pairs can be written inline; a text input is always taken as written."""
    import json

    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"smalls: {pair!r} is not key=value")
        field = spec.args.model_fields.get(key)
        if field is not None and field.annotation in (str, str | None):
            out[key] = value
            continue
        try:
            out[key] = json.loads(value)
        except ValueError:
            out[key] = value
    return out


# One plain call per tool for `smalls tools smoke`. A tool that needs a handle or a name takes it from an
# earlier result, so nothing here names anything in a bundle.
SMOKE_ARGUMENTS = {"list_alerts": {"since": "-2h"}, "search_logs": {"since": "-30m"}, "list_changes": {"since": "-24h"},
                   "read_chat": {"since": "-2h"}, "search_knowledge": {"query": "what to check first when requests are slow"},
                   "itsm_query": {"table": "incident"}}


def smoke(built: dict, ctx) -> int:
    """Call every built tool once and print one line per tool. Exits non-zero if any call is an error."""
    from smalls.tools import registry

    failed = 0
    seen: list[str] = []
    for name, spec in sorted(built.items(), key=lambda item: item[0] == "get_record"):      # get_record last: it needs a handle
        arguments = {"handle": seen[0]} if name == "get_record" and seen else SMOKE_ARGUMENTS.get(name, {})
        if name == "query_metrics":         # what to ask for is data: the first series the inventory would list
            from smalls.tools import derive

            served = derive.metric_queries(ctx)[:1]
            arguments = {"metric": served[0]["metric"], "scope": ",".join(sorted(served[0]["scope"])), "since": "-1h"} if served else {}
        if name == "get_topology":          # and here the first item the CMDB lists
            from smalls.tools import derive

            first = derive.one(ctx, "SELECT title FROM visible WHERE kind = 'ci' ORDER BY title LIMIT 1")
            arguments = {"entity": first["title"] if first else "", "hops": 2}
        env = registry.call(spec, arguments, ctx)
        seen += list(env.shown)
        failed += env.status != "ok"
        print(f"{name:<18} {env.status:<6} {len(env.rows):>3} rows {len(env.referenced):>3} referenced {len(env.text) // 4:>5} tokens"
              + ("" if env.status == "ok" else f"   {env.text.splitlines()[0][:120]}"))
    return 1 if failed else 0


def cmd_tools(args: argparse.Namespace) -> int:
    import json
    from datetime import datetime, timezone

    from smalls.tools import registry, store

    tags = store.tags_enabled() if args.tags is None else args.tags == "on"
    surface = registry.surface(tags=tags)
    built = registry.registry(tags=tags)
    if args.tools_command == "list":
        for name, spec in surface.items():
            cap = f"{spec.cap_tokens} tokens" + (f", {spec.cap_rows} rows" if spec.cap_rows else "")
            print(f"{name:<18} {'built' if name in built else 'not built':<10} cap {cap}")
        return 0
    if args.tools_command == "schema":
        names = [args.name] if args.name else list(surface)
        unknown = [n for n in names if n not in surface]
        if unknown:
            raise SystemExit(f"smalls: no tool named {unknown[0]}; the tools are: {', '.join(surface)}")
        print(json.dumps([registry.definition(surface[n]) for n in names], indent=2, ensure_ascii=False))
        return 0
    if args.tools_command == "call" and args.name not in built:
        raise SystemExit(f"smalls: no tool named {args.name}; the tools that are built: {', '.join(built)}")
    s = store.build(_bundle(args.bundle), tags=tags)
    now = s.set_now(args.now)
    ctx = registry.ToolContext(store=s, now=datetime.fromtimestamp(now / 1000, timezone.utc), tags=tags)
    if args.tools_command == "smoke":
        return smoke(built, ctx)
    spec = built[args.name]
    env = registry.call(spec, tool_arguments(spec, args.arguments), ctx)
    print(env.text)
    print(f"--- status: {env.status}")
    print(f"--- shown ({len(env.shown)}): {' '.join(env.shown)}")
    print(f"--- referenced ({len(env.referenced)}): {' '.join(env.referenced)}")
    return 0 if env.status == "ok" else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smalls", description="Smalls: incident investigation over raw operational evidence.")
    sub = p.add_subparsers(dest="command", required=True)

    tools = sub.add_parser("tools", help="call one tool against a bundle, or print the tool surface")
    tools_sub = tools.add_subparsers(dest="tools_command", required=True)
    call = tools_sub.add_parser("call", help="call a tool at a given now and print its result and its handles")
    call.add_argument("name")
    call.add_argument("arguments", nargs="*", metavar="key=value", help="a value is read as JSON when it parses as JSON")
    call.add_argument("--now", required=True, metavar="TIME", help="the replay clock: HH:MM on the bundle's day, or an ISO time")
    call.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"), help="bundle folder (or SMALLS_BUNDLE)")
    listing = tools_sub.add_parser("list", help="the eleven tools, which are built, and their caps")
    schema = tools_sub.add_parser("schema", help="the tool definitions the model is given")
    schema.add_argument("name", nargs="?")
    smoke_cmd = tools_sub.add_parser("smoke", help="call every built tool once at a given now; one line per tool")
    smoke_cmd.add_argument("--now", required=True, metavar="TIME")
    smoke_cmd.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"), help="bundle folder (or SMALLS_BUNDLE)")
    for command in (call, listing, schema, smoke_cmd):
        command.add_argument("--tags", choices=["on", "off"], help="with or without the tag inputs (default: SMALLS_TAGS)")
    tools.set_defaults(run=cmd_tools)

    ingest = sub.add_parser("ingest", help="build the in-memory store from a bundle and print the ingest report")
    ingest.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"), help="bundle folder (or SMALLS_BUNDLE)")
    ingest.add_argument("--dump", metavar="x.db", help="also write the store to a SQLite file, for inspection")
    ingest.add_argument("--now", metavar="HH:MM", help="set the replay clock and report what is visible then")
    ingest.set_defaults(run=cmd_ingest)

    tag = sub.add_parser("tag", help="call the classifier for every tag-cache miss (needs JEV_API_KEY)")
    tag.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"), help="bundle folder (or SMALLS_BUNDLE)")
    tag.add_argument("--no-repeats", action="store_true", help="fill the cache only; skip the stability repeats")
    tag.add_argument("--report", action="store_true", help="offline: print the precision, coverage and stability table")
    tag.add_argument("--sample", action="store_true", help="offline: print the sample to label by hand, as YAML")
    tag.add_argument("--experiment", metavar="spec.toml", help="ask a file of candidate facets instead; answers go beside it, not in the cache")
    tag.set_defaults(run=cmd_tag)

    # `smalls run`, `smalls resume` and `smalls replay` are the agent's own commands under this name.
    from smalls.agent.__main__ import add_run_commands

    add_run_commands(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
