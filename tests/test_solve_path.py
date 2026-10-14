"""The scripted solve path (build row D5 of docs/decisions/tools-agent-build.md): about fifteen tool calls
that reach the cause of the development incident at its second checkpoint. Offline: the store and the tools."""
import shlex

import golden
import parity
from eval.solve import run as solve
from smalls import cli
from smalls.tools import registry


def test_the_scripted_path_holds_step_by_step_at_the_second_checkpoint(capsys):
    path = solve.load()
    assert path["bundle"] == parity.BUNDLE.name and golden.CHECKPOINTS[golden.SECOND].startswith(path["now"]) and 12 <= len(path["steps"]) <= 18
    results = solve.run(golden.ctx(golden.SECOND), path["steps"])
    assert [r["n"] for r in results if not r["ok"]] == [], [(r["n"], r["tool"], r["missing"]) for r in results if not r["ok"]]
    assert all(r["status"] == "ok" for r in results) and sum(r["tokens"] for r in results) < 15_000
    # It uses the tools a person would: most of the surface, and every step says why it is made.
    assert len({step["tool"] for step in path["steps"]}) >= 9 and all(step["why"].strip() and step["expect"] for step in path["steps"])
    assert set(step["tool"] for step in path["steps"]) <= set(registry.registry(tags=False))
    assert solve.main(["--bundle", str(parity.BUNDLE)]) == 0
    out = capsys.readouterr().out
    assert f"{len(results)} of {len(results)} steps hold" in out and "The path reaches: " in out


def test_each_step_is_printed_as_the_command_that_makes_the_same_call():
    path = solve.load()
    tools = registry.surface(tags=False)
    for step in path["steps"]:
        words = shlex.split(solve.command(step, path["now"]))
        assert words[:4] == ["smalls", "tools", "call", step["tool"]] and words[4:6] == ["--now", path["now"]]
        assert cli.tool_arguments(tools[step["tool"]], words[6:]) == step["args"], step["tool"]     # the CLI reads it back as the same arguments


def test_the_path_is_not_shown_at_the_first_checkpoint_and_nothing_under_smalls_reads_it():
    path = solve.load()
    early = solve.run(golden.ctx(golden.FIRST), path["steps"])
    assert not all(r["ok"] for r in early)                                           # some of what it reads did not exist yet
    for source in (parity.ROOT / "smalls").rglob("*"):
        if source.is_file() and source.suffix in (".py", ".md", ".yaml", ".toml"):
            text = source.read_text(encoding="utf-8")
            assert "eval.solve" not in text and "eval/solve" not in text and "path.yaml" not in text, source
