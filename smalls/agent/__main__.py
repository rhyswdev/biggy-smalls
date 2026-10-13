"""The agent's entry point.

    python -m smalls.agent schema [--tags on|off] [--size] [--write] [--skeleton]

`schema` prints every tool schema the model can be shown and the report schema, as one JSON document.
`--size` prints the size of the cached prefix (the system prompt and the tool definitions) with tags on
and off. `--write` regenerates `smalls/contracts/report.schema.json`. `--skeleton` prints the descriptions
file for the current surface with every text a placeholder.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import yaml

from smalls.agent import internal_tools, prompts
from smalls.contracts import report
from smalls.contracts.report import Rationale
from smalls.tools import descriptions, registry, store


def surface(*, tags: bool) -> dict[str, registry.ToolSpec]:
    """Every tool the investigator can be shown, built or not, in the fixed order."""
    return internal_tools.toolset(registry.surface(tags=tags))


def skeleton() -> dict[str, Any]:
    """The descriptions file's keys for the whole surface, with every tag input present."""
    return descriptions.skeleton({name: spec.args for name, spec in surface(tags=True).items()}, gated=Rationale)


def definitions(*, tags: bool, texts: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [registry.definition(spec, texts) for spec in surface(tags=tags).values()]


def schema_document(*, tags: bool, texts: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tags": "on" if tags else "off", "tools": definitions(tags=tags, texts=texts), "report": report.report_schema()}


def prefix_size(*, tags: bool) -> dict[str, int]:
    """The cached prefix, in characters, and in tokens counted as characters over 4."""
    system = len(prompts.load("investigator"))
    tools = len(json.dumps(definitions(tags=tags), ensure_ascii=False))
    return {"system_prompt_chars": system, "tool_definitions_chars": tools, "prefix_chars": system + tools,
            "prefix_tokens": (system + tools) // 4}


def cmd_schema(args: argparse.Namespace) -> int:
    if args.write:
        report.SCHEMA_PATH.write_text(report.schema_text(), encoding="utf-8")
        print(f"wrote {report.SCHEMA_PATH}")
        return 0
    if args.skeleton:
        print(yaml.safe_dump(skeleton(), sort_keys=False, allow_unicode=True, width=110), end="")
        return 0
    if args.size:
        print(json.dumps({"tags_on": prefix_size(tags=True), "tags_off": prefix_size(tags=False)}, indent=2))
        return 0
    tags = store.tags_enabled() if args.tags is None else args.tags == "on"
    print(json.dumps(schema_document(tags=tags), indent=2, ensure_ascii=False))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m smalls.agent", description="The Smalls investigator.")
    sub = p.add_subparsers(dest="command", required=True)
    schema = sub.add_parser("schema", help="print every tool schema and the report schema")
    schema.add_argument("--tags", choices=["on", "off"], help="with or without the tag inputs (default: SMALLS_TAGS)")
    schema.add_argument("--size", action="store_true", help="print the size of the cached prefix, tags on and off")
    schema.add_argument("--write", action="store_true", help="regenerate the committed report schema file")
    schema.add_argument("--skeleton", action="store_true", help="print the descriptions file with every text a placeholder")
    schema.set_defaults(run=cmd_schema)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
