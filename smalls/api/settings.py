"""The API's settings, from the environment (docs/decisions/ui-build.md section 5).

| Variable              | Meaning                                                                          |
|-----------------------|----------------------------------------------------------------------------------|
| SMALLS_API_MODE       | `live` (the configured profile; a key is needed) or `scripted` (no key)          |
| SMALLS_BUNDLE         | the bundle folder the runs investigate                                           |
| SMALLS_CHECKPOINT     | the trigger file the runs start from, which is also the clock: `1452` is 14:52   |
| SMALLS_API_RUNS       | the committed run records loaded at start (default `eval/runs`)                  |
| SMALLS_API_REGISTRY   | a module with `registry()` and `inventory()`, in place of the bundle's tools     |
| SMALLS_API_SCRIPT     | the script the scripted profile plays (default the generic dev script)           |

Nothing here names a bundle, an incident or a model: the Makefile passes the bundle and the checkpoint.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent.parent
MODES = ("live", "scripted")
DEV_SCRIPT = "smalls.api.devscript:turn"


@dataclass(frozen=True)
class Settings:
    bundle: Path
    checkpoint: str
    mode: Literal["live", "scripted"] = "live"
    runs_dir: Path = ROOT / "eval" / "runs"
    registry: str | None = None
    script: str = DEV_SCRIPT
    workers: int = 4
    tags: bool | None = None
    load_scripted: bool = False         # load committed runs of the scripted provider too; tests only (R5)
    db_name: str = field(default_factory=lambda: f"smalls-{uuid.uuid4().hex[:12]}")

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.environ.get("SMALLS_API_MODE", "live").strip().lower()
        if mode not in MODES:
            raise SystemExit(f"SMALLS_API_MODE must be live or scripted, not {mode!r}")
        bundle, checkpoint = os.environ.get("SMALLS_BUNDLE"), os.environ.get("SMALLS_CHECKPOINT")
        if not bundle or not checkpoint:
            raise SystemExit("the API needs SMALLS_BUNDLE and SMALLS_CHECKPOINT; `make api` passes both")
        return cls(bundle=Path(bundle), checkpoint=checkpoint, mode=mode,  # type: ignore[arg-type]
                   runs_dir=Path(os.environ.get("SMALLS_API_RUNS") or ROOT / "eval" / "runs"),
                   registry=os.environ.get("SMALLS_API_REGISTRY") or None, script=os.environ.get("SMALLS_API_SCRIPT") or DEV_SCRIPT)

    @property
    def uri(self) -> str:
        """The one database of this process: SQLite's in-memory VFS, so every connection opened by this URI
        shares it, and it is gone when the last one closes."""
        return f"file:/{self.db_name}?vfs=memdb"

    def trigger(self) -> str:
        """The incident commander's note the checkpoint starts from: the bundle's trigger file of that name."""
        return (Path(self.bundle) / "triggers" / f"{self.checkpoint}.md").read_text(encoding="utf-8").strip()

    def now(self) -> datetime:
        """The checkpoint's clock time on the bundle's day: `1452` is 14:52."""
        from smalls.tools.store import Bundle, Store

        text = self.checkpoint
        clock = f"{text[:-2]}:{text[-2:]}" if text.isdigit() and len(text) in (3, 4) else text
        ms = Store(None, Bundle(self.bundle)).resolve_time(clock)
        return datetime.fromtimestamp(ms / 1000, timezone.utc)
