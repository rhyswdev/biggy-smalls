"""PLAN 12's controls: the bundle-string lint reads every facts.yaml it finds, so a second bundle's names are
caught on the agent side too. The second facts file here is planted in a temporary tree."""
import yaml

from smalls_data import prompt_lint


def facts(company: str, person: str, entity: str) -> dict:
    first, last = person.split()
    return {"company": company, "bundle": f"{company.lower()}-day", "entities": {entity: {"aliases": {"splunk": entity}}},
            "people": [{"name": person, "handle": f"{first.lower()}.{last.lower()}"}]}


def test_a_string_from_a_second_facts_file_is_caught(tmp_path):
    (tmp_path / "scenario").mkdir()
    (tmp_path / "scenario" / "facts.yaml").write_text(yaml.safe_dump(facts("Oakmoor", "Ada Brightwell", "ledger-api")))
    (tmp_path / "bundles" / "other").mkdir(parents=True)
    (tmp_path / "bundles" / "other" / "facts.yaml").write_text(yaml.safe_dump(facts("Quillfen", "Tobias Marrow", "vault-svc")))
    strings, files = prompt_lint.every_bundle_strings(tmp_path)
    assert [f.relative_to(tmp_path).as_posix() for f in files] == ["bundles/other/facts.yaml", "scenario/facts.yaml"]
    assert {"Quillfen", "Tobias Marrow", "vault-svc", "Oakmoor"} <= set(strings)
    texts = [("smalls/agent/prompts/x.md", "Check the vault-svc pool first.")]
    assert prompt_lint.find(texts, strings) == ["smalls/agent/prompts/x.md contains the bundle string 'vault-svc'"]
    dev_only = prompt_lint.bundle_strings(yaml.safe_load((tmp_path / "scenario" / "facts.yaml").read_text()))
    assert prompt_lint.find(texts, dev_only) == []      # the development facts alone miss it


def test_the_cli_reads_every_facts_file_and_fails_on_a_hit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prompt_lint, "every_bundle_strings", lambda: (["vault-svc"], [tmp_path / "facts.yaml"]))
    monkeypatch.setattr(prompt_lint, "prompt_texts", lambda: [("smalls/agent/prompts/x.md", "the vault-svc pool")])
    from smalls_data import ui_lint

    monkeypatch.setattr(ui_lint, "ui_texts", lambda: [])
    assert prompt_lint.main(["--every-bundle"]) == 1
    assert "FAIL smalls/agent/prompts/x.md contains the bundle string 'vault-svc'" in capsys.readouterr().out
