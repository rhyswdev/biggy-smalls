"""The API's OpenAPI document, from the app object, with no server running: `python -m smalls.api.openapi`
prints it. The web app's types are generated from the committed copy (`web/lib/openapi.json`), and a test
fails when that copy differs from what the app says now."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent.parent.parent / "web" / "lib" / "openapi.json"


def document() -> dict:
    from smalls.api.app import create_app

    return create_app().openapi()


def text() -> str:
    return json.dumps(document(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        SNAPSHOT.write_text(text(), encoding="utf-8")
        print(f"wrote {SNAPSHOT}")
    else:
        sys.stdout.write(text())
