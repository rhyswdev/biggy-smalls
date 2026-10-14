"""The UI's bundle-string lint (docs/decisions/ui-build.md section 7.6): no source file of the API or the web
app holds a string from the dataset. Every incident fact the UI shows comes from served data at `now` or from a
run, never from code (ruling U10), and the designs' sample data, which is this incident's answer, never enters
the code. It reads every source file under `smalls/api/` and `web/`, comments included, and skips installed
packages, build output and the generated API types. It runs in `make lint` as invariant 17.
"""

from __future__ import annotations

from pathlib import Path

from smalls_data import prompt_lint

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = ("smalls/api", "web")
SKIP_DIRS = {"node_modules", ".next", "__pycache__", "test-results", "playwright-report"}
SKIP_FILES = {"web/lib/api-types.ts", "web/lib/openapi.json", "web/pnpm-lock.yaml", "web/next-env.d.ts"}
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".css", ".json", ".md", ".yaml", ".yml", ".toml", ".html", ".txt"}


def ui_texts(root: Path | None = None) -> list[tuple[str, str]]:
    root = root or ROOT
    out = []
    for folder in FOLDERS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if not path.is_file() or SKIP_DIRS & set(path.relative_to(root).parts) or rel in SKIP_FILES or path.suffix not in SUFFIXES:
                continue
            out.append((rel, path.read_text(encoding="utf-8", errors="replace")))
    return out


def find(texts: list[tuple[str, str]] | None = None, strings: list[str] | None = None) -> list[str]:
    return prompt_lint.find(ui_texts() if texts is None else texts, strings)


if __name__ == "__main__":
    hits = find()
    print("\n".join(hits) if hits else f"ui lint: {len(ui_texts())} files, no bundle string")
    raise SystemExit(1 if hits else 0)
