"""The API's settings, from the environment (docs/decisions/ui-build.md section 5).

| Variable              | Meaning                                                                          |
|-----------------------|----------------------------------------------------------------------------------|
| SMALLS_API_MODE       | `live` (the configured profile; a key is needed) or `scripted` (no key)          |
| SMALLS_BUNDLE         | the bundle folder the runs investigate                                           |
| SMALLS_CHECKPOINT     | the trigger file the runs start from, which is also the clock: `1452` is 14:52   |
| SMALLS_API_RUNS       | the committed run records loaded at start (default `eval/runs`)                  |
| SMALLS_API_REGISTRY   | a module with `registry()` and `inventory()`, in place of the bundle's tools     |
| SMALLS_API_SCRIPT     | the script the scripted profile plays (default the generic dev script); the     |
|                       | name `concluded` picks the dev script that reaches a probable cause             |
| SMALLS_INCIDENT       | the incident number the card shows, when served data cannot say which it is      |
| SMALLS_API_SCRIPT_DELAY | scripted mode only: seconds per streamed word, so a run can be watched          |
| SMALLS_API_DRAFTER    | the profile that drafts the stakeholder messages (U8), or `code`; by default the |
|                       | mid-tier profile in live mode and code in scripted mode                          |
| SMALLS_API_DRAFT_SCRIPT | the script a `scripted` drafter plays (default the generic dev drafter)        |
| SMALLS_API_OUTCOMES_PROFILE | the profile that writes one clause per check after a run (v4 plan 5.1), or    |
|                       | `code`; by default the fast profile in live mode and code in scripted mode       |
| SMALLS_API_OUTCOMES_SCRIPT | the script a `scripted` summariser plays (default the generic dev one)      |
| SMALLS_API_OVERVIEW_PROFILE | the profile that writes an imported incident's overview (5.2), or `code`; by  |
|                       | default the mid-tier profile in live mode and code in scripted mode              |
| SMALLS_API_OVERVIEW_SCRIPT | the script a `scripted` overview writer plays (default the generic dev one) |
| SMALLS_COMMS_CADENCE  | minutes between stakeholder updates by priority, `1=30,2=60` by default: "next    |
|                       | update due" is the ticket's last update plus this (v4 plan 5.2)                  |

Nothing here names a bundle, an incident or a model: the Makefile passes the bundle and the checkpoint.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent.parent.parent
MODES = ("live", "scripted")
DEV_SCRIPT = "smalls.api.devscript:turn"
DEV_DRAFTER = "smalls.api.devscript:draft"
DEV_OUTCOMES = "smalls.api.devscript:outcomes"
DEV_OVERVIEW = "smalls.api.devscript:overview"
SCRIPTS = {"concluded": "smalls.api.devscript:concluded", "dev": DEV_SCRIPT}     # short names for SMALLS_API_SCRIPT
OUTCOMES = "anthropic-small"            # v4 plan 5.1: summarising, not reasoning, so the provider's fast tier
DRAFTER = "anthropic-mid"               # U8: the mid-tier profile in the agent's config
CODE = "code"


def cadence(text: str) -> tuple[tuple[str, int], ...]:
    """`1=30,2=60` as pairs; a malformed entry stops the start with a message."""
    try:
        return tuple((k.strip(), int(v)) for k, v in (pair.split("=", 1) for pair in text.split(",") if pair.strip()))
    except ValueError:
        raise SystemExit(f"SMALLS_COMMS_CADENCE must look like 1=30,2=60, not {text!r}") from None


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
    clock: str | None = None            # an ISO time in place of the checkpoint's clock: for a fixture registry with its own day
    incident: str | None = None         # the incident number, when served data cannot say which incident the trigger belongs to
    script_delay: float = 0.0           # scripted mode: seconds per streamed word, to watch a run unfold
    drafter: str | None = None          # a profile name, or `code`; None picks by mode (`drafter_profile`)
    draft_script: Any = DEV_DRAFTER     # "module:function"; a test may pass the callable itself
    outcomes: str | None = None         # a profile name, or `code`; None picks by mode (`outcomes_profile`)
    outcomes_script: Any = DEV_OUTCOMES
    overview: str | None = None         # a profile name, or `code`; None picks by mode (`overview_profile`)
    overview_script: Any = DEV_OVERVIEW
    cadence: tuple[tuple[str, int], ...] = (("1", 30), ("2", 60))     # [comms]: priority code -> minutes between updates
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
                   registry=os.environ.get("SMALLS_API_REGISTRY") or None,
                   script=SCRIPTS.get(os.environ.get("SMALLS_API_SCRIPT") or "", os.environ.get("SMALLS_API_SCRIPT") or DEV_SCRIPT),
                   outcomes=os.environ.get("SMALLS_API_OUTCOMES_PROFILE") or None,
                   outcomes_script=os.environ.get("SMALLS_API_OUTCOMES_SCRIPT") or DEV_OUTCOMES,
                   overview=os.environ.get("SMALLS_API_OVERVIEW_PROFILE") or None,
                   overview_script=os.environ.get("SMALLS_API_OVERVIEW_SCRIPT") or DEV_OVERVIEW,
                   incident=os.environ.get("SMALLS_INCIDENT") or None,
                   script_delay=float(os.environ.get("SMALLS_API_SCRIPT_DELAY") or 0),
                   drafter=os.environ.get("SMALLS_API_DRAFTER") or None, draft_script=os.environ.get("SMALLS_API_DRAFT_SCRIPT") or DEV_DRAFTER,
                   **({"cadence": cadence(os.environ["SMALLS_COMMS_CADENCE"])} if os.environ.get("SMALLS_COMMS_CADENCE") else {}))

    def next_update(self, priority_code: str | None, last_update: str) -> str | None:
        """When the next stakeholder update is due: the last update plus the cadence for the priority, or None."""
        from datetime import timedelta

        minutes = dict(self.cadence).get(priority_code or "")
        if minutes is None:
            return None
        at = datetime.fromisoformat(last_update.replace("Z", "+00:00")) + timedelta(minutes=minutes)
        return at.strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def overview_profile(self) -> str:
        """Who writes the overview's prose: the named profile, else the mid-tier one in live mode and code without a key."""
        return self.overview or (DRAFTER if self.mode == "live" else CODE)

    @property
    def outcomes_profile(self) -> str:
        """Who writes the per-check clauses: the named profile, else the fast one in live mode and code without a key."""
        return self.outcomes or (OUTCOMES if self.mode == "live" else CODE)

    @property
    def drafter_profile(self) -> str:
        """Who drafts the messages: the named profile, else the mid-tier one in live mode and code without a key."""
        return self.drafter or (DRAFTER if self.mode == "live" else CODE)

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

        if self.clock:
            return datetime.fromisoformat(self.clock.replace("Z", "+00:00"))
        text = self.checkpoint
        clock = f"{text[:-2]}:{text[-2:]}" if text.isdigit() and len(text) in (3, 4) else text
        ms = Store(None, Bundle(self.bundle)).resolve_time(clock)
        return datetime.fromtimestamp(ms / 1000, timezone.utc)
