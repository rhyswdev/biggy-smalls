"""The findings lint (docs/decisions/eval-loop.md, row L5): only generic text enters an iteration room."""
import pytest

from smalls_data.findings_lint import problems
from smalls_data.prompt_lint import bundle_strings

GENERIC = """F-03 · prompt · seen in 4 of 5 runs
What happened: a rival hypothesis was rejected without reading any record that contradicts it.
What the method says: a rejected rival names the rows that clear it.
Fixed when: rejected rivals carry an `against` row in at least 4 of 5 runs.
Keep: the opening sweep and the order of reads, which work."""


def test_a_generic_finding_is_clean():
    assert problems(GENERIC, "f.md") == []


@pytest.mark.parametrize("extra, check", [
    (" It started at 14:26.", "clock time"),
    (" On 2026-10-13.", "date"),
    (" Version 2.14.3 shipped.", "version number"),
    (" See run sol-base-C-1452-tags-r1.", "run id"),
    (" Seen in 20261014T090933Z.", "run id"),
    (" It cited log:pay-01/5ad16319.", "handle"),
])
def test_a_finding_with_incident_detail_is_refused(extra, check):
    assert f"{check}: f.md" in problems(GENERIC + extra, "f.md")


def test_a_bundle_string_is_refused():
    assert "bundle string: f.md" in problems(GENERIC + " " + bundle_strings()[0], "f.md")
