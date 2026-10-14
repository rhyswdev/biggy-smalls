"""The hypothesis board: validation, confidence caps, the status line, and the ready and stale checks. Pure
functions over state (docs/research/agent-design.md section 2.0; J1, J2 and J14 in
docs/decisions/tools-agent-build.md).

The model writes judgements; code writes the bookkeeping and enforces the caps.

- A new hypothesis must carry its statement, role, status, subject, confidence and source. `source` and
  `role` are set when a hypothesis is created and cannot change (J1): the role is what the hypothesis
  claims, and `status` says whether that claim stands, so a cleared rival keeps its role and is rejected.
- With no non-chat support, confidence is capped at 0.5: the value is clamped and the result says so.
- At most one hypothesis is `leading`: a second is refused.
- Ready (J2): one hypothesis leads; it has a non-chat handle in `support` and in `discriminating`; every
  other live hypothesis whose role is trigger or contributing has non-chat support; and every other
  hypothesis whose role is cause has an `against` handle. A handle that entered the run as carry-forward
  does not count.
- Stale: a live hypothesis nobody has touched for two rounds is flagged; when nothing on the board has
  changed for `stale_rounds` rounds the gate stops approving reads and actions.
"""

from __future__ import annotations

from typing import Any, Iterable

from smalls.contracts.handles import handle_class

CAP_WITHOUT_NON_CHAT = 0.5
REQUIRED_ON_CREATE = ("statement", "role", "status", "subject", "confidence", "source")
STALE_FLAG_ROUNDS = 2
STALE = "the board has not changed in {n} rounds; record what the evidence shows with update_board, or submit now"


def non_chat(handles: Iterable[str], skip: frozenset[str] = frozenset()) -> list[str]:
    return [h for h in handles or [] if handle_class(h) != "chat" and h not in skip]


def apply_update(board: dict[str, dict[str, Any]], updates: list[Any], *, round: int, call_id: str,
                 allow_new: bool = True) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Check hypothesis updates against the board. Returns (the partial updates to merge, problems, notes).
    With any problem nothing is merged. A note is something code changed and says so, such as a clamped
    confidence."""
    merged = {hid: dict(h) for hid, h in board.items()}
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    notes: list[str] = []
    for upd in updates:
        fields = upd.model_dump(exclude_unset=True, exclude_none=True)
        hid = fields.pop("id")
        prev = merged.get(hid)
        if prev is None:
            if not allow_new:
                problems.append(f"{hid}: the board holds no hypothesis with this id; create it with update_board first")
                continue
            missing = [name for name in REQUIRED_ON_CREATE if name not in fields]
            if missing:
                problems.append(f"{hid}: a new hypothesis needs {', '.join(missing)}")
                continue
        elif "source" in fields and fields["source"] != prev.get("source"):
            problems.append(f"{hid}: source is set when a hypothesis is created and cannot change (it is {prev.get('source')})")
            continue
        elif "role" in fields and fields["role"] != prev.get("role"):
            problems.append(f"{hid}: role is what the hypothesis claims, set when it is created; it cannot change (it is {prev.get('role')}). "
                            "A different claim is a new hypothesis")
            continue
        after = {**(prev or {}), **fields}
        if after.get("confidence", 0) > CAP_WITHOUT_NON_CHAT and not non_chat(after.get("support")):
            notes.append(f"{hid}: confidence {after['confidence']:.2f} clamped to {CAP_WITHOUT_NON_CHAT:.2f}, because nothing "
                         "but chat supports it")
            fields["confidence"] = after["confidence"] = CAP_WITHOUT_NON_CHAT
        merged[hid] = after
        out[hid] = {**out.get(hid, {}), **fields, "round": round, "call_id": call_id}
    leading = [hid for hid, h in merged.items() if h.get("status") == "leading"]
    if len(leading) > 1:
        problems.append(f"only one hypothesis can be leading, and this would make {len(leading)}: {', '.join(leading)}. "
                        "Set the others to open or rejected in the same call")
    return ({} if problems else out), problems, notes


def leading(board: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    return next((h for h in board.values() if h.get("status") == "leading"), None)


def missing_for_ready(board: dict[str, dict[str, Any]], carried: frozenset[str] = frozenset()) -> list[str]:
    """What stands between the board and "ready to report", in fixed words. Empty means ready."""
    lead = leading(board)
    if lead is None:
        return ["no leading hypothesis"]
    missing = []
    if not non_chat(lead.get("support"), carried):
        missing.append(f"leading {lead['id']} has no non-chat support")
    if not non_chat(lead.get("discriminating"), carried):
        missing.append(f"leading {lead['id']} has no discriminating evidence")
    for hid, h in board.items():
        if h is lead or h.get("status") == "rejected" and h.get("role") != "cause":
            continue
        if h.get("role") in ("trigger", "contributing") and h.get("status") != "rejected" and not non_chat(h.get("support"), carried):
            missing.append(f"{hid} ({h['role']}) has no non-chat support")
        if h.get("role") == "cause" and not h.get("against"):
            missing.append(f"rival {hid} has no against")
    return missing


def last_change(board: dict[str, dict[str, Any]]) -> int:
    """The last round in which anything on the board moved."""
    return max((row.get("round") or 0 for h in board.values() for row in h.get("history", [])), default=0)


def stale_ids(board: dict[str, dict[str, Any]], round: int) -> list[str]:
    """Live hypotheses nobody has touched for two rounds."""
    out = []
    for hid, h in board.items():
        touched = max((row.get("round") or 0 for row in h.get("history", [])), default=0)
        if h.get("status") != "rejected" and round - touched >= STALE_FLAG_ROUNDS:
            out.append(hid)
    return out


def stale_refusal(board: dict[str, dict[str, Any]], round: int, stale_rounds: int, since: int = 0, skip: Iterable[int] = ()) -> str | None:
    """The reason the gate refuses reads and actions, once `stale_rounds` whole rounds have passed with no
    change. `round` is the round being gated, so the rounds already finished are the ones before it. `since`
    is a round the count starts again from: a follow-up on a finished run starts with a quiet board. `skip`
    holds rounds that do not count: a round whose every call was malformed and answered with its error."""
    base = max(last_change(board), since)
    quiet = (round - 1) - base - sum(1 for r in set(skip) if base < r < round)
    return STALE.format(n=quiet) if quiet >= stale_rounds else None


def status_line(board: dict[str, dict[str, Any]], *, round: int, used: int, limit: int, turns_left: int,
                carried: frozenset[str] = frozenset()) -> str:
    """The one line code appends to each round's last tool result. It has one form:
    `[board: ready | not ready: <missing>; stale: <ids>; budget <used>/<limit>, <n> turns left]`."""
    missing = missing_for_ready(board, carried)
    parts = ["board: ready" if not missing else "board: not ready: " + ", ".join(missing)]
    stale = stale_ids(board, round)
    if stale:
        parts.append("stale: " + ", ".join(stale))
    parts.append(f"budget {used}/{limit}, {turns_left} turns left")
    return "[" + "; ".join(parts) + "]"


def for_report(board: dict[str, dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
    """The board's hypotheses as the report holds them. `history` is the per-checkpoint view: the rows
    earlier checkpoints left, then one row for this run, with every handle that moved the hypothesis."""
    out = []
    for h in board.values():
        because = list(dict.fromkeys(handle for row in h.get("history", []) for handle in row.get("because") or []))
        earlier = [row for row in h.get("checkpoints", []) if row.get("as_of") != as_of]
        out.append({"id": h["id"], "statement": h["statement"], "role": h["role"], "status": h["status"], "subject": h["subject"],
                    "entities": list(h.get("entities") or []), "changes": list(h.get("changes") or []), "confidence": h["confidence"],
                    "support": list(h.get("support") or []), "against": list(h.get("against") or []), "source": h["source"],
                    "history": [*earlier, {"as_of": as_of, "status": h["status"], "confidence": h["confidence"], "because": because}]})
    return out
