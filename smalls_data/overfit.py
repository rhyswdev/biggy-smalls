"""The overfit review (ruling U15 in docs/decisions/ui-build.md): every change to text a model reads, or to
code under the agent, its tools and its contracts, is read against this incident's answer before it is
committed. Generic practice passes; anything shaped to the answer is flagged.

    python -m smalls_data.overfit --bundle <folder> [--base <ref>]

It takes the diff from `--base` (default `HEAD`) to the working tree over the guarded paths, untracked
files included, splits it into hunks, and makes one model call with the set's `overfit` role. The
reviewer is given the answer key and PLAN sections 1.1 and 1.5. It prints a verdict per hunk (pass or flag,
why, and what in the answer it maps to), the call's cost, and exits 1 on any flag, 2 when it cannot review.

This is the one place on the build side that reads the answer, and nothing on the agent side imports it.
The prompt beside it names no model; the profile comes from the agent's config, so a key is needed, and
under pytest the profile is the scripted one unless SMALLS_LIVE=1.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROMPT = Path(__file__).with_name("overfit_prompt.md")
# Text a model reads and the code that decides what it sees: the agent, its tools (tag profiles included),
# its contracts, and the drafting prompt the API sends.
GUARDED = ("smalls/agent", "smalls/tools", "smalls/contracts", "smalls/api/prompts")
PLAN_SECTIONS = (("### 1.1 ", "### 1.2 "), ("### 1.5 ", "## 2. "))
TOOL = {"name": "submit_review", "description": "One verdict for every hunk of the diff.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["verdicts"], "properties": {
            "verdicts": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["hunk", "verdict", "reason", "maps_to"],
                "properties": {"hunk": {"type": "integer", "description": "The hunk's number."},
                               "verdict": {"type": "string", "enum": ["pass", "flag"]},
                               "reason": {"type": "string", "description": "One sentence: why."},
                               "maps_to": {"type": "string", "description": "What in the answer it maps to; `nothing` for a pass."}}}}}}}
NO_VERDICT = "the reviewer gave no verdict for this hunk"


@dataclass(frozen=True)
class Hunk:
    n: int
    path: str
    header: str
    text: str

    def render(self) -> str:
        return f"[hunk {self.n}] {self.path} {self.header}\n{self.text}"


def _git(*args: str, ok: tuple[int, ...] = (0,)) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if done.returncode not in ok:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def diff(base: str = "HEAD", paths: tuple[str, ...] = GUARDED) -> str:
    """The diff from `base` to the working tree over `paths`, with untracked files as new files."""
    out = _git("diff", "--no-color", "--no-ext-diff", base, "--", *paths)
    for path in _git("ls-files", "--others", "--exclude-standard", "--", *paths).splitlines():
        if path.strip():
            out += _git("diff", "--no-color", "--no-index", "--", "/dev/null", path, ok=(0, 1))
    return out


def hunks(text: str) -> list[Hunk]:
    """A unified diff as numbered hunks, each with its file. A file with no `@@` hunk (a binary or a mode
    change) is one hunk of its header lines."""
    found: list[Hunk] = []
    for block in re.split(r"(?m)^(?=diff --git )", text):
        if not block.strip():
            continue
        m = re.search(r"(?m)^\+\+\+ (?:b/)?(.+)$", block) or re.search(r"(?m)^--- (?:a/)?(.+)$", block)
        path = m[1].strip() if m and m[1].strip() != "/dev/null" else block.split()[2].removeprefix("a/")
        parts = re.split(r"(?m)^(?=@@ )", block)
        if len(parts) == 1:
            found.append(Hunk(len(found) + 1, path, "(no hunk)", block.strip()))
            continue
        for part in parts[1:]:
            header, _, body = part.partition("\n")
            found.append(Hunk(len(found) + 1, path, header.split(" @@")[0] + " @@", body.rstrip("\n")))
    return found


def file_hunks(paths: list[str]) -> list[Hunk]:
    """Whole files as new text, one hunk each: a finding is reviewed before it enters an iteration room."""
    return [Hunk(n, path, "(whole file)", "\n".join("+" + line for line in Path(path).read_text(encoding="utf-8").splitlines()))
            for n, path in enumerate(paths, 1)]


def plan_sections(plan: Path = ROOT / "docs" / "PLAN.md") -> str:
    """PLAN sections 1.1 (the true chain) and 1.5 (the traps), cut by their headings."""
    text, out = plan.read_text(encoding="utf-8"), []
    for start, end in PLAN_SECTIONS:
        i = text.index(start)
        out.append(text[i:text.index(end, i)].rstrip())
    return "\n\n".join(out)


def user_turn(found: list[Hunk], answer: str, plan: str) -> str:
    return "\n\n".join([
        "THE ANSWER: the answer key of the development incident.", "```yaml\n" + answer.strip() + "\n```",
        "THE ANSWER, as the plan states it: the true causal chain and the traps.", plan,
        f"THE DIFF: {len(found)} hunks. Judge each one.", *("```diff\n" + h.render() + "\n```" for h in found)])


def parse(message: Any) -> list[dict[str, Any]]:
    """The verdicts from the reviewer's `submit_review` call, or from a JSON object in its text."""
    for call in getattr(message, "tool_calls", None) or []:
        if call.get("name") == TOOL["name"]:
            return list(call["args"].get("verdicts") or [])
    content = message.content if isinstance(message.content, str) else "".join(
        b.get("text", "") for b in message.content if isinstance(b, dict) and b.get("type") == "text")
    m = re.search(r"\{.*\}", content, re.S)
    try:
        return list(json.loads(m[0]).get("verdicts") or []) if m else []
    except ValueError:
        return []


def judge(found: list[Hunk], verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per hunk. A hunk the reviewer skipped is a flag: an unreviewed change is not a passed one."""
    given = {int(v.get("hunk", -1)): v for v in verdicts if str(v.get("hunk", "")).lstrip("-").isdigit()}
    rows = []
    for h in found:
        v = given.get(h.n) or {"verdict": "flag", "reason": NO_VERDICT, "maps_to": "unknown"}
        verdict = "pass" if str(v.get("verdict")).lower() == "pass" else "flag"
        rows.append({"hunk": h.n, "path": h.path, "header": h.header, "verdict": verdict, "reason": str(v.get("reason") or ""),
                     "maps_to": str(v.get("maps_to") or "")})
    return rows


def review(found: list[Hunk], answer: str, plan: str, *, profile: str | None = None, script: Any = None) -> dict[str, Any]:
    """One model call over every hunk. Returns the rows, the model that answered and the call's cost."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from smalls.agent.config import SCRIPTED, load_config
    from smalls.agent.models import as_model_call_error, make_model, provider_tools
    from smalls.agent.record import cost

    cfg = load_config(profile=profile)
    chosen = cfg.investigator if profile else (cfg.role("overfit") or cfg.investigator)     # the set's `overfit` role
    spec = chosen.with_params(script=script) if chosen.provider == SCRIPTED else chosen
    model, bind = make_model(spec, timeout_s=cfg.timeout_s)
    bound = model.bind_tools(provider_tools([TOOL], spec.provider, strict=False), **bind)
    try:
        message = bound.invoke([SystemMessage(PROMPT.read_text(encoding="utf-8")), HumanMessage(user_turn(found, answer, plan))])
    except Exception as error:      # recorded without any key value it may quote
        raise SystemExit(f"overfit-review: the model call failed: {as_model_call_error(error)}") from None
    name = message.response_metadata.get("model_name") or spec.model
    usd = cost({"usage": message.usage_metadata or {}}, cfg.price(name))
    return {"rows": judge(found, parse(message)), "model": name, "profile": spec.profile, "cost_usd": usd,
            "usage": dict(message.usage_metadata or {})}


def report(result: dict[str, Any]) -> str:
    lines = []
    for row in result["rows"]:
        lines.append(f"{row['verdict'].upper():<4}  [{row['hunk']}] {row['path']} {row['header']}")
        lines.append(f"      {row['reason']}")
        if row["verdict"] == "flag":
            lines.append(f"      maps to: {row['maps_to']}")
    flagged = sum(1 for row in result["rows"] if row["verdict"] == "flag")
    lines.append(f"overfit-review: {len(result['rows'])} hunks, {flagged} flagged; profile {result['profile']}, "
                 f"model {result['model']}, cost ${result['cost_usd']:.4f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m smalls_data.overfit", description=__doc__.split("\n\n")[0])
    p.add_argument("--bundle", required=True, help="the bundle whose answer key the review reads")
    p.add_argument("--base", default="HEAD", help="review the diff from this ref to the working tree (default HEAD)")
    p.add_argument("--profile", help="a model profile from the agent's config (default: the set's overfit role)")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--file", action="append", help="review this file whole as new text, in place of the diff (a finding); repeatable")
    args = p.parse_args(argv)
    found = file_hunks(args.file) if args.file else hunks(diff(args.base))
    if not found:
        print(f"overfit-review: no change under {', '.join(GUARDED)} since {args.base}; nothing to review")
        return 0
    answer = (Path(args.bundle) / "_internal" / "answer_key.yaml").read_text(encoding="utf-8")
    result = review(found, answer, plan_sections(), profile=args.profile)
    print(json.dumps(result, indent=1) if args.json else report(result))
    return 1 if any(row["verdict"] == "flag" for row in result["rows"]) else 0


if __name__ == "__main__":
    sys.exit(main())
