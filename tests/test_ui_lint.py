"""Lint invariant 17 (docs/decisions/ui-build.md section 7.6): the UI's code holds no bundle string, and the lint
would see one if it did, in any source file of the API or the web app, comments included."""

from pathlib import Path

from smalls_data import prompt_lint, ui_lint


def test_the_ui_holds_no_bundle_string():
    assert ui_lint.find() == []


def test_the_lint_reads_every_kind_of_source_and_skips_build_output(tmp_path: Path):
    seed = prompt_lint.SEEDS[0]
    (tmp_path / "web/components").mkdir(parents=True)
    (tmp_path / "web/node_modules/x").mkdir(parents=True)
    (tmp_path / "smalls/api").mkdir(parents=True)
    (tmp_path / "web/components/Card.tsx").write_text(f"// a comment naming {seed}\nexport const x = 1;\n")
    (tmp_path / "smalls/api/thing.py").write_text("X = 1  # nothing here\n")
    (tmp_path / "web/node_modules/x/index.js").write_text(f"'{seed}'")
    texts = ui_lint.ui_texts(tmp_path)
    assert [where for where, _ in texts] == ["smalls/api/thing.py", "web/components/Card.tsx"]
    assert ui_lint.find(texts, []) == [f"web/components/Card.tsx contains the bundle string {seed!r}"]
