"""The manifest: one line per record version in a bundle, written by the generator to
`_internal/manifest.jsonl`. Never served to the agent. Used by the lint, the leak test, the scorer, and
tests that check the tool layer ingests every record at the right time.

    {"handle":"log:pay-02/3fa91c0e","src":"splunk","path":"splunk/raw/payments_app/pay-02.log","locator":4412,
     "t_utc":"2026-10-13T14:31:07.221Z","t_known":"2026-10-13T14:31:07.221Z","valid_from":null,"valid_to":null,
     "class":"log.hikari_timeout","sig_id":"sig.first_pool_timeout","kind":"signal"}

`locator` is the 1-based first line for line-oriented files (logs, JSONL) and a JSON pointer for records
inside a JSON document (`/result/3`). `t_utc` is when the thing happened; `t_known` is the earliest time an
observer could see this record, and visibility is decided on `t_known`. They differ for a backdated record
(a change raised after the deploy it covers, a status-page incident whose `started_at` predates publication)
and for a metric bucket, which is known when it closes. `valid_from`/`valid_to` bound a record version for sources that are
rewound (ServiceNow fields through sys_audit, status-page snapshots); null means unbounded.
`kind` is signal, trap or noise. Noise rows carry a `tag` in the scenario YAML (background, defect, or the
trap they serve); the manifest keeps only the class.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from smalls.contracts.handles import is_handle

Kind = Literal["signal", "trap", "noise"]


def _check_utc(v: str | None) -> str | None:
    if v is None:
        return v
    if not v.endswith("Z"):
        raise ValueError(f"time must be UTC with a Z suffix: {v}")
    datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


class ManifestRow(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    handle: str
    src: str
    path: str
    locator: int | str
    t_utc: str
    t_known: str
    valid_from: str | None = None
    valid_to: str | None = None
    class_: str
    sig_id: str | None = None
    kind: Kind

    # "class" is a Python keyword; serialise under its contract name.
    def to_json(self) -> str:
        d = self.model_dump()
        d["class"] = d.pop("class_")
        order = ["handle", "src", "path", "locator", "t_utc", "t_known", "valid_from", "valid_to", "class", "sig_id", "kind"]
        return json.dumps({k: d[k] for k in order}, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "ManifestRow":
        d = json.loads(line)
        d["class_"] = d.pop("class")
        return cls(**d)

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: str) -> str:
        if not is_handle(v):
            raise ValueError(f"not a valid handle: {v}")
        return v

    @field_validator("t_utc", "t_known", "valid_from", "valid_to")
    @classmethod
    def _utc(cls, v: str | None) -> str | None:
        return _check_utc(v)

    @field_validator("locator")
    @classmethod
    def _locator(cls, v: int | str) -> int | str:
        if isinstance(v, int) and v < 1:
            raise ValueError("line locators are 1-based")
        if isinstance(v, str) and not v.startswith("/"):
            raise ValueError("string locators are JSON pointers")
        return v

    @field_validator("sig_id")
    @classmethod
    def _sig(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("sig."):
            raise ValueError("sig_id must start with 'sig.'")
        return v

    @field_validator("class_")
    @classmethod
    def _class(cls, v: str) -> str:
        if "." not in v:
            raise ValueError("class is '<family>.<name>', e.g. log.hikari_timeout")
        return v


def manifest_json_schema() -> dict:
    s = ManifestRow.model_json_schema(by_alias=False)
    props = s["properties"]
    props["class"] = props.pop("class_")
    s["required"] = ["class" if r == "class_" else r for r in s.get("required", [])]
    return s
