"""The `smalls` command.

    smalls ingest --bundle <folder> [--dump x.db] [--now HH:MM]   build the store, print the ingest report
    smalls tag    --bundle <folder> [--measure]                   compute tag answers into the committed cache

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
    _bundle(args.bundle)
    raise SystemExit("smalls ingest: the store arrives with build row 2 of docs/decisions/ingestion-decisions.md")


def cmd_tag(args: argparse.Namespace) -> int:
    _bundle(args.bundle)
    raise SystemExit("smalls tag: the tagging pass arrives with build row 6 of docs/decisions/ingestion-decisions.md")


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
    tag.set_defaults(run=cmd_tag)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
