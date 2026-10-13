"""The `smalls` command.

    smalls ingest --bundle <folder> [--dump x.db] [--now HH:MM]   build the store, print the ingest report
    smalls tag    --bundle <folder> [--report | --sample]         compute tag answers into the committed cache

`ingest` reads only the bundle and the committed tag cache: no key, no network. `tag` is the only command
that calls the classifier and the only one that needs JEV_API_KEY. `smalls tools call` arrives with the tools.
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

        print(measure.skeleton(measure.units_of(store.read(bundle)[1])), end="")
        return 0
    from smalls.tools.ingest.tagging import run

    if args.report:
        print(run.report(bundle))
        return 0
    return run.main(bundle, repeats=not args.no_repeats)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="smalls", description="Smalls: incident investigation over raw operational evidence.")
    sub = p.add_subparsers(dest="command", required=True)

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
    tag.set_defaults(run=cmd_tag)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
