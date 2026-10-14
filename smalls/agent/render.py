"""Terminal rendering of the event protocol, and the interactive gate.

Every string that came from a tool, a log line, a chat message or the model is rendered as text: it is
escaped before it reaches the console, so markup inside evidence is never interpreted.

The renderer keeps nothing between events but whether it is in the middle of a line of reasoning. What a
round's closing bar shows, the board's leading hypothesis and the budget, is carried by the event that
closes the round. So a run that pauses in one process and resumes in another prints what a replay of its
record prints.

A report's handles are numbered in the order it first cites them, and listed under it. `show` prints the
row behind a number or a handle, as the run was shown it.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markup import escape as esc
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from smalls.agent.events import Event
from smalls.agent.gate import NOTE_KEY
from smalls.contracts.handles import is_handle

MARK = {"approved": "[green]✓[/]", "rejected": "[red]✗[/]", "deferred": "[yellow]…[/]"}
COLOUR = {"ok": "green", "error": "red", "rejected": "red", "deferred": "yellow", "repeat": "dim"}


def who(by: dict[str, Any] | None) -> str:
    return f"{by['who']} via {by['via']}" if by else "nobody"


def citations(report: dict[str, Any]) -> list[str]:
    """A report's handles, in the order it first cites them: the summary, then each hypothesis, the
    timeline, the actions, the do-not list and the alert verdicts."""
    found: list[str] = []
    for token in str(report.get("summary") or "").split():
        word = token.strip("()[]{}<>,.;:!?\"'`")
        found.append(word if is_handle(word) else word.rstrip(":"))
    found = [h for h in found if is_handle(h)]
    for h in report.get("hypotheses") or []:
        found += [*(h.get("support") or []), *(h.get("against") or [])]
    for name in ("timeline", "actions", "do_not"):
        for row in report.get(name) or []:
            found += row.get("evidence") or []
    found += [a["handle"] for a in report.get("alerts") or []]
    return list(dict.fromkeys(found))


def show(record: Any, wanted: str, console: Console | None = None) -> int:
    """Print the row behind a handle, or behind a number of the report's citation list, as the run was shown it.
    `record` is a `RunRecord`, or the folder of one in the file layout."""
    from smalls.agent.record import FileBackend, RunRecord

    if not isinstance(record, RunRecord):
        record = RunRecord(None, record.resolve().name, backend=FileBackend(record.resolve().parent))
    c = console or Console(highlight=False)
    read = lambda name: record.read_text(name) or ""      # noqa: E731
    report = json.loads(read("report.json") or "null") or {}
    numbered = citations(report)
    handle = numbered[int(wanted) - 1] if wanted.isdigit() and 0 < int(wanted) <= len(numbered) else wanted
    rows = [json.loads(line) for line in read("evidence.jsonl").splitlines() if line.strip()]
    mine = [row for row in rows if row["handle"] == handle]
    if not mine:
        raise SystemExit(f"{wanted}: no tool result in this run showed that" + (f" (the report cites {len(numbered)} handles)" if wanted.isdigit() else ""))
    row = mine[-1]
    number = f"[{numbered.index(handle) + 1}] " if handle in numbered else ""
    c.print(f"[bold]{esc(number)}{esc(handle)}[/]  [dim]shown by {esc(row['call_id'])}, known {esc(str(row.get('t_known')))}"
            + (f", and by {len(mine) - 1} other call(s)" if len(mine) > 1 else "") + "[/]")
    c.print(esc(row["text"]), soft_wrap=True)
    return 0


class Renderer:
    def __init__(self, console: Console | None = None, show_thinking: bool = True):
        self.c = console or Console(highlight=False)
        self.show_thinking = show_thinking
        self._in_thinking = False

    def _end_stream(self) -> None:
        if self._in_thinking:
            self.c.print()
            self._in_thinking = False

    def emit(self, ev: Event) -> None:
        d, t = ev.data, ev.type
        if t == "thinking_delta":
            if self.show_thinking:
                if not self._in_thinking:
                    self.c.print("[dim]thinking[/] ", end="")
                    self._in_thinking = True
                self.c.print(f"[dim italic]{esc(d['text'])}[/]", end="", soft_wrap=True)
            return
        self._end_stream()
        if t == "run_started":
            self.c.rule(f"run {esc(ev.run_id)}")
            self.c.print(f"now {esc(d['now'])}  profile {esc(str(d.get('profile')))}  auto-approve {'on' if d.get('auto_approve') else 'off'}"
                         f"  tags {'on' if d.get('tags') else 'off'}")
            self.c.print(f"[bold]Trigger:[/] {esc(d['trigger'])}")
        elif t == "phase":
            self.c.print(f"[cyan]── {esc(d['name'])}[/]")
        elif t == "text_delta":
            self.c.print(esc(d["text"]), end="")
        elif t == "model_call":
            u = d.get("usage", {})
            self.c.print(f"[dim]model call {d['latency_ms']} ms · in {u.get('input_tokens')} out {u.get('output_tokens')} · "
                         f"${d.get('cost_usd', 0):.4f} · {esc(str(d.get('stop_reason')))}[/]")
        elif t == "tool_call_proposed":
            args = json.dumps(d["args"], ensure_ascii=False)
            self.c.print(f"[yellow]▶ {esc(d['tool'])}[/] [dim]{esc(d['kind'])}[/] {esc(args[:160])}")
            rationale = d.get("rationale")
            if isinstance(rationale, dict):
                self.c.print(f"  [dim]tests: {esc(str(rationale.get('tests')))} · expects: {esc(str(rationale.get('expect')))}[/]")
        elif t == "gate_pending":
            self.c.print(f"[magenta]⏸ gate: {len(d['calls'])} call(s) need a decision[/]")
        elif t == "gate_decision":
            if d["by"]["who"] != "policy" or d["decision"] != "approved":
                extra = f": {esc(str(d.get('reason_text') or d['reason']))}" if d.get("reason") else ""
                wait = f" after {d['wait_ms']} ms" if d.get("wait_ms") is not None else ""
                self.c.print(f"  {MARK[d['decision']]} {esc(d['tool'])} {d['decision']} by {who(d['by'])}{wait}{extra}")
        elif t == "ic_note":
            self.c.print(f"  [bold]note[/] {esc(d['handle'])}  {esc(d['text'])}")
        elif t == "tool_result":
            self.c.print(f"  [{COLOUR.get(d['status'], 'white')}]← {esc(d['tool'])} {d['status']}[/] {len(d['shown'])} shown, "
                         f"{len(d['referenced'])} referenced, {d['chars']} chars, {d['latency_ms']} ms"
                         + (f"  [dim]{esc(d['native_query'])}[/]" if d.get("native_query") else ""))
        elif t == "hypothesis_update":
            self.c.print(f"  [bold]board[/] {esc(d['id'])} {esc(str(d.get('role')))} {d['status']} {d['confidence']:.2f}: {esc(d['statement'])}")
        elif t == "board_status":
            self.c.print(f"  [dim]{esc(d['line'])}[/]")
        elif t == "heartbeat":
            self.c.print(f"[dim]… still working: nothing new for {d.get('seconds')} s[/]")
        elif t == "budget":
            b = d.get("board")
            if b is not None:       # the bar that closes a round, drawn from this event alone
                lead = b.get("leading")
                leading = f"leading {esc(lead['id'])} {float(lead.get('confidence') or 0):.2f}" if lead else "no leading hypothesis"
                self.c.rule(f"[dim]round {d.get('round')} · {leading} · {b.get('open')} open, {b.get('rejected')} rejected · "
                            f"calls {d.get('used')}/{d.get('limit')} · {d.get('turns_left')} turns left[/]", style="dim")
        elif t == "restart":
            self.c.print(f"[cyan]── restarted from the board[/] [dim]({esc(str(d.get('reason')))}; thread {esc(str(d.get('thread')))})[/]")
        elif t == "stuck":
            self.c.print(f"  [yellow]stuck[/] the last {d['empty_reads']} reads returned nothing; a note may help")
        elif t == "cache_ratio_low":
            self.c.print(f"  [yellow]cache[/] only {d['ratio']:.0%} of the input was read from cache (floor {d['floor']:.0%}): cost is climbing")
        elif t == "validation":
            self.c.print(f"  [{'green' if d.get('ok') else 'red'}]validator[/] {esc(json.dumps(d, ensure_ascii=False))}")
        elif t == "verifier_verdict":
            decision = d.get("decision") or {}
            self.c.print(f"  [bold]verifier[/] {esc(str(d.get('status')))}: {len(decision.get('unsupported') or [])} leading claim(s) not supported, "
                         f"{len(decision.get('checks') or [])} new check(s), {len(decision.get('discarded_checks') or [])} already run or invalid"
                         + (f"; disputed: {esc(', '.join(d['disputed']))}" if d.get("disputed") else ""))
        elif t == "report":
            self._report(d["report"], d.get("status"))
        elif t == "run_paused":
            self.c.rule("paused" + (f": {esc(d['reason'])}" if d.get("reason") else " at a gate"))
            self.c.print(f"resume with: {esc(d['resume'])}")
        elif t == "run_finished":
            self.c.rule(esc(str(d["status"])))
        elif t == "error":
            self.c.print(f"[red]error[/] {esc(json.dumps(d, default=str, ensure_ascii=False))}")

    def _report(self, r: dict[str, Any], status: str | None) -> None:
        self.c.print(Panel(esc(r["summary"]), title=f"report as of {esc(str(r['as_of']))} ({esc(str(status))})"))
        numbered = citations(r)
        cite = lambda handles: " ".join(f"[{numbered.index(h) + 1}]" for h in handles or [] if h in numbered)      # noqa: E731
        table = Table(show_header=True, header_style="bold")
        for column in ("id", "role", "status", "conf", "statement", "support", "against"):
            table.add_column(column)
        for h in r["hypotheses"]:
            table.add_row(esc(h["id"]), esc(h["role"]), esc(h["status"]), f"{h['confidence']:.2f}", esc(h["statement"]),
                          esc(cite(h["support"])), esc(cite(h["against"])))
        self.c.print(table)
        for row in r.get("timeline", []):
            self.c.print(f"  [dim]{esc(str(row.get('t_utc')))}[/] {esc(str(row.get('event')))} [dim]{esc(cite(row.get('evidence')))}[/]")
        for a in r.get("actions", []):
            self.c.print(f"  {a['rank']}. {esc(a['action'])} [dim]({esc(a['owner'])}) {esc(cite(a['evidence']))}[/]")
        for row in r.get("do_not", []):
            self.c.print(f"  [red]do not[/] {esc(row['action'])}: {esc(row['why'])} [dim]{esc(cite(row.get('evidence')))}[/]")
        for a in r.get("alerts", []):
            self.c.print(f"  [dim]alert {esc(cite([a['handle']]))} {esc(str(a.get('verdict')))}[/]")
        if numbered:
            self.c.print("  [dim]citations (show <run> <number> prints the row):[/]")
            for n, handle in enumerate(numbered, 1):
                self.c.print(f"  [dim]{esc(f'[{n}]')} {esc(handle)}[/]")
        for p in r.get("proposed_actions", []):
            self.c.print(f"  proposal {esc(p['tool'])} {esc(json.dumps(p['args'], ensure_ascii=False))}: [bold]{p['decision']}[/] by {who(p['by'])}"
                         + (f" ({esc(str(p['reason']))})" if p.get("reason") else ""))
        for q in r.get("open_questions", []):
            self.c.print(f"  [dim]open: {esc(q)}[/]")
        checked = r.get("validation") or {}
        if checked.get("verifier"):
            self.c.print(f"  [dim]verifier: {esc(checked['verifier'])}" + (f"; disputed: {esc(', '.join(checked['disputed']))}" if checked.get("disputed") else "") + "[/]")


def interactive_decider(console: Console | None = None):
    """Ask a person at the terminal, one card per call."""
    c = console or Console(highlight=False)

    def decide(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out = {}
        if cards and all(card.get("by") == "harness" for card in cards):     # the opening sweep is one card
            listed = "\n".join(f"{esc(card['tool'])} {esc(json.dumps(card['args'], ensure_ascii=False))}" for card in cards)
            c.print(Panel(listed, title=f"run the opening sweep? ({len(cards)} reads)"))
            answer = Prompt.ask(esc("[a]pprove or [r]eject"), choices=["a", "r"], default="a", console=c)
            reason = None if answer == "a" else Prompt.ask("reason (goes back to the agent)", console=c)
            return {card["call_id"]: ({"approve": True} if answer == "a" else {"approve": False, "reason": reason}) for card in cards}
        for card in cards:
            rationale = card.get("rationale") or {}
            c.print(Panel(f"[bold]{esc(card['tool'])}[/] ({esc(card['kind'])})\n{esc(json.dumps(card['args'], indent=1, ensure_ascii=False))}\n\n"
                          f"tests: {esc(str(rationale.get('tests', '(none given)')))}\nexpects: {esc(str(rationale.get('expect', '(none given)')))}",
                          title="approve this call?"))
            answer = Prompt.ask(esc("[a]pprove, [r]eject, [d]efer, or add a [n]ote first"), choices=["a", "r", "d", "n"], default="a", console=c)
            if answer == "n":       # a note does not answer the card: it rides along, and the agent sees it next round
                out[NOTE_KEY] = Prompt.ask("note for the agent", console=c)
                answer = Prompt.ask(esc("[a]pprove, [r]eject or [d]efer"), choices=["a", "r", "d"], default="a", console=c)
            if answer == "a":
                out[card["call_id"]] = {"approve": True}
            elif answer == "d":
                out[card["call_id"]] = {"defer": True}
            else:
                out[card["call_id"]] = {"approve": False, "reason": Prompt.ask("reason (goes back to the agent)", console=c)}
        return out

    return decide
