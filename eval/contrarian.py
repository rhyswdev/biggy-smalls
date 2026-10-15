"""The contrarian control (PLAN 12's controls; row Z5 of docs/decisions/eval-loop.md section 15).

    python -m eval.contrarian run --bundle <folder> --now <time> --trigger-file <path> [any `smalls.agent run` option]

Arm K: the agent of arm C, the same graph, tools, verifier and run record, with one difference: its investigator
prompt is the frozen one followed by `eval/controls/contrarian.md`, the development incident's discriminators
(PLAN 1.5) written as rules, and its cause besides. It is a prompt that has memorised the development incident.
It should reach the development cause; on a held-out variant, where a lesson is made false, it must fail, or the
variant does not test whether the agent follows the evidence.

The rules are added in this process only: nothing under `smalls/agent/` changes, and the run record's
`prompt_sha` differs from the frozen agent's, so a control run can never pass for an agent run. The rules file is
not agent text, so the bundle-string lint does not read it and the overfit review does not apply: it is the
overfit, on purpose.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "eval" / "controls" / "contrarian.md"
ARM = "K"


def install(rules: Path = RULES) -> None:
    """Make `prompts.load("investigator")` return the frozen text followed by the rules."""
    from smalls.agent import prompts

    frozen = prompts.load
    extra = rules.read_text(encoding="utf-8")

    def load(name: str) -> str:
        return frozen(name) + extra if name == "investigator" else frozen(name)

    prompts.load = load


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "run":
        raise SystemExit("usage: python -m eval.contrarian run <the options of `python -m smalls.agent run`>")
    if "--arm" in argv:
        raise SystemExit("eval.contrarian: the arm is K; leave --arm out")
    install()
    from smalls.agent.__main__ import main as agent_main

    return agent_main([*argv[:1], "--arm", ARM, *argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
