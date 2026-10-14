"""The findings lint (docs/decisions/eval-loop.md, row L5): a finding enters an iteration room only in generic
form. It must carry no bundle string, no handle, no clock time or date, no version number and no run id: the
author is blind to the incident, and a finding is the one text that crosses from the eval side into the room.

    python -m smalls_data.findings_lint <file>...

It prints one line per problem, naming the check and the file, and exits 1 on any. The overfit reviewer then
reads the finding too (`python -m smalls_data.overfit --file <finding>`).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from smalls.contracts.handles import is_handle
from smalls_data.prompt_lint import find

CHECKS = (
    ("clock time", re.compile(r"(?<![\d.:])\d{1,2}:\d{2}(?::\d{2})?(?!\d)")),
    ("date", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("version number", re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?!\.?\w)")),
    ("run id", re.compile(r"\b\d{8}T\d{6}Z\b|\b[\w-]+-[AC]-\d{4}-tags-r\d+\b")),
)


def problems(text: str, where: str) -> list[str]:
    out = [f"bundle string: {where}" for _ in find([(where, text)])[:1]]
    out += [f"{name}: {where}" for name, pattern in CHECKS if pattern.search(text)]
    words = {w.strip(".,;:()[]{}'\"`") for w in text.split()}
    if any(is_handle(w) for w in words if w):
        out.append(f"handle: {where}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m smalls_data.findings_lint", description=__doc__.split("\n\n")[0])
    p.add_argument("files", nargs="+")
    args = p.parse_args(argv)
    found = [line for f in args.files for line in problems(Path(f).read_text(encoding="utf-8"), f)]
    print("\n".join(found) if found else f"findings lint: {len(args.files)} file(s) clean")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
