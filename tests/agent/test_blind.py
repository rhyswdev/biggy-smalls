"""The blind prompt pass, its mechanical side (build row C2; docs/decisions/tools-agent-build.md section 3):
the room holds exactly what the author may read, the checks say only the name of a check and a file, and
the five files go in byte for byte. These tests hold before the blind pass and after it."""
import json
import re
from pathlib import Path

import pytest
import yaml

from smalls.agent import __main__ as cli
from smalls.agent import blind, prompts
from smalls.agent.config import ROOT
from smalls.tools import descriptions
from smalls_data import prompt_lint

ROOM_FILES = ["BRIEF.md", "agent-design.md", "descriptions.yaml", "first_turn.md", "investigator.md", "prompt-v0.txt", "requirements.md",
              "schema.json", "tools-agent-build.md", "verifier_claims.md", "verifier_contrarian.md"]
RECORD = ROOT / "docs/decisions/tools-agent-build.md"


@pytest.fixture
def room(tmp_path: Path, capsys) -> Path:
    folder = tmp_path / "room"
    assert cli.main(["blind-room", str(folder)]) == 0
    capsys.readouterr()
    return folder


def fill(folder: Path) -> None:
    """Stand in for an author: replace every placeholder with some text."""
    for name in blind.PROMPT_FILES:
        text = (folder / name).read_text().replace(prompts.PLACEHOLDER, f"Text for {name}.")
        (folder / name).write_text(text)
    texts = yaml.safe_load((folder / "descriptions.yaml").read_text())

    def write(node):
        for key, value in node.items():
            if isinstance(value, dict):
                write(value)
            else:
                node[key] = f"What {key} is."
    write(texts)
    texts["action_kinds"] = {"restart": "Restart a component.", "rollback": "Return to an earlier version."}
    (folder / "descriptions.yaml").write_text(yaml.safe_dump(texts, sort_keys=False))


def test_the_brief_the_command_writes_is_the_records_word_for_word():
    record = RECORD.read_text()
    section = record.split("The brief, as `blind-room` writes it:", 1)[1]
    in_record = re.search(r"```text\n(.*?)```", section, re.S).group(1)
    assert blind.BRIEF == in_record


def test_the_room_holds_exactly_what_the_author_may_read(room):
    assert sorted(p.name for p in room.iterdir()) == ROOM_FILES
    assert (room / "BRIEF.md").read_text() == blind.BRIEF
    for name, source in blind.DOCS.items():
        assert (room / name).read_bytes() == (ROOT / source).read_bytes()
    assert (room / "prompt-v0.txt").read_text().startswith("You investigate production incidents") and "update_hypotheses" in (room / "prompt-v0.txt").read_text()
    for name in ("investigator.md", "verifier_claims.md", "verifier_contrarian.md"):
        assert (room / name).read_text() == "PLACEHOLDER\n"
    assert prompts.slots_of((room / "first_turn.md").read_text()) == list(prompts.SLOTS)
    texts = yaml.safe_load((room / "descriptions.yaml").read_text())
    assert {v for _, v in descriptions.paths(texts)} == {"PLACEHOLDER"} and texts == cli.skeleton()


def test_the_rooms_schema_shows_structure_only_with_tags_on(room):
    doc = json.loads((room / "schema.json").read_text())
    assert doc["tags"] == "on" and len(doc["tools"]) == 15 and doc["report"]["properties"]["hypotheses"]

    def described(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "description" and isinstance(value, str):
                    yield value
                else:
                    yield from described(value)
        elif isinstance(node, list):
            for item in node:
                yield from described(item)

    assert set(described(doc["tools"])) == {"PLACEHOLDER"}                         # placeholders where descriptions go
    tag_inputs = {t["name"] for t in doc["tools"] if {"signal", "act", "change_kind"} & set(t["parameters"]["properties"])}
    assert tag_inputs == {"list_alerts", "search_logs", "list_changes", "itsm_query", "read_chat"}


def test_nothing_in_the_room_names_the_incident_or_a_model(room):
    texts = [(name, (room / name).read_text()) for name in ROOM_FILES]
    assert prompt_lint.find(texts) == []
    model = re.compile(r"claude-(opus|sonnet|haiku|fable|\d)|gpt-\d|gemini-\d", re.I)
    assert not [name for name, text in texts if model.search(text)]


def test_the_room_must_be_a_new_folder_outside_the_repository(tmp_path, room):
    with pytest.raises(SystemExit, match="inside the repository"):
        cli.main(["blind-room", str(ROOT / "eval" / "room")])
    with pytest.raises(SystemExit, match="inside the repository"):
        cli.main(["blind-room", str(ROOT)])
    with pytest.raises(SystemExit, match="not empty"):
        cli.main(["blind-room", str(room)])
    assert not (ROOT / "eval" / "room").exists()


def test_the_checks_name_a_check_and_a_file_and_nothing_else(room, capsys):
    assert blind.check(room, cli.skeleton()) == [f"placeholder gate: {name}" for name in blind.TARGETS]
    fill(room)
    assert blind.check(room, cli.skeleton()) == []
    assert cli.main(["blind-check", str(room)]) == 0 and "all pass" in capsys.readouterr().out
    template = room / "first_turn.md"
    good = template.read_text()
    for broken in (good.replace("{budget}", ""), good + "{now}", good + "{surprise}", good + "{"):
        template.write_text(broken)
        assert blind.check(room, cli.skeleton()) == ["slot check: first_turn.md"]
    template.write_text(good)
    texts = yaml.safe_load((room / "descriptions.yaml").read_text())
    del texts["tools"]["get_record"]["params"]["part"]
    (room / "descriptions.yaml").write_text(yaml.safe_dump(texts, sort_keys=False))
    assert blind.check(room, cli.skeleton()) == ["descriptions coverage: descriptions.yaml"]
    (room / "descriptions.yaml").write_text("tools: [unclosed")
    assert blind.check(room, cli.skeleton()) == ["descriptions file does not parse: descriptions.yaml"]
    (room / "investigator.md").unlink()
    assert blind.check(room, cli.skeleton()) == ["missing file: investigator.md"]
    assert cli.main(["blind-check", str(room)]) == 1


def test_install_copies_the_five_files_byte_for_byte_and_only_when_the_checks_pass(room, tmp_path, monkeypatch, capsys):
    target = tmp_path / "installed"
    (target / "prompts").mkdir(parents=True)
    monkeypatch.setattr(blind, "installed", lambda: {**{n: target / "prompts" / n for n in blind.PROMPT_FILES}, "descriptions.yaml": target / "descriptions.yaml"})
    monkeypatch.setattr(blind, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="not installed"):
        cli.main(["blind-install", str(room)])                                     # still placeholders
    assert not list((target / "prompts").iterdir())
    fill(room)
    assert cli.main(["blind-install", str(room)]) == 0
    for name in blind.PROMPT_FILES:
        assert (target / "prompts" / name).read_bytes() == (room / name).read_bytes()
    assert (target / "descriptions.yaml").read_bytes() == (room / "descriptions.yaml").read_bytes()
    assert capsys.readouterr().out.count("installed ") == 5


def test_a_live_run_refuses_to_start_while_a_placeholder_remains(monkeypatch):
    monkeypatch.setattr(prompts, "placeholders", lambda: ["investigator.md"])
    with pytest.raises(SystemExit, match="still hold placeholder text: investigator.md"):
        cli._check_placeholders("anthropic")
    cli._check_placeholders("scripted")                                            # an offline run does not care
    monkeypatch.setattr(prompts, "placeholders", lambda: [])
    monkeypatch.setattr(descriptions, "placeholders", lambda texts=None: ["tools.x.description"])
    with pytest.raises(SystemExit, match="descriptions.yaml"):
        cli._check_placeholders("anthropic")
    monkeypatch.setattr(descriptions, "placeholders", lambda texts=None: [])
    cli._check_placeholders("anthropic")


def test_the_installed_files_are_all_placeholders_or_pass_every_check():
    """Before the blind pass every file is a placeholder; after it, none is and every check passes. There
    is no half state, and the first-turn template has its five slots either way."""
    findings = blind.check(None, cli.skeleton())
    assert findings == [] or findings == [f"placeholder gate: {name}" for name in blind.TARGETS]
    assert prompts.slot_problems() == []
    assert prompts.first_turn(now="N", inventory="I", trigger="T", budget="B", prior_board="P").count("N") >= 1
