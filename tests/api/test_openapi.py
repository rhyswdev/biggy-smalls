"""The web app's API types come from `web/lib/openapi.json`; this fails when that copy differs from the app's own
schema, so a change to an endpoint or a body cannot leave the web app's types behind (section 7.3)."""

from smalls.api import openapi


def test_the_committed_schema_is_the_apps():
    assert openapi.SNAPSHOT.read_text(encoding="utf-8") == openapi.text(), "run `make api-types`"
