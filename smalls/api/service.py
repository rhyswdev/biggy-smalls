"""What the API holds for the life of the process: the settings, the database, the agent's config, the
worker pool the runs execute on, and the runners of the runs that are still going (docs/decisions/ui-build.md
section 6.2). One instance per app, built at start and closed at shutdown."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from smalls.agent.config import Config, load_config
from smalls.agent.graph import iso
from smalls.api.db import Database
from smalls.api.settings import Settings

# Every seam of section 8 is built (rows R0c), so the UI shows each feature that stands on one.
SEAMS = {"S1": "web decisions", "S2": "approve with edit", "S3": "follow-ups continue the run", "S4": "notes while running",
         "S5": "stop", "S6": "auto-approve mid-run", "S7": "native query before a read", "S8": "run records in the database"}


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.uri)
        self.cfg: Config = load_config(profile="scripted" if settings.mode == "scripted" else None)
        self.now: datetime = settings.now()
        self.pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="smalls-run")
        self.runners: dict[str, Any] = {}               # run id -> the Runner of a run that has not finished
        self.locks: dict[str, threading.Lock] = {}      # run id -> the lock every resume of it takes
        self.closing = False

    def capabilities(self) -> dict[str, Any]:
        return {"mode": self.settings.mode, "checkpoint": self.settings.checkpoint, "now": iso(self.now),
                "profile": self.cfg.investigator.profile, "seams": {k: True for k in SEAMS}, "seam_names": SEAMS}

    def close(self) -> None:
        """Stop taking runs and let the paused ones go: they live only in this process's database (U2)."""
        self.closing = True
        for runner in list(self.runners.values()):
            try:
                runner.request_stop()
            except Exception:
                pass
        self.pool.shutdown(wait=True, cancel_futures=True)
        for runner in list(self.runners.values()):
            try:
                runner.close()
            except Exception:
                pass
        self.runners.clear()
        self.db.close()
