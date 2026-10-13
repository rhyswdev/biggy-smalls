"""Terminal rendering of the event protocol, and the interactive gate.

Every string that came from a tool, a log line, a chat message or the model is rendered as text: it is
escaped before it reaches the console, so markup inside evidence is never interpreted.
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

MARK = {"approved": "[green]✓[/]", "rejected": "[red]✗[/]", "deferred": "[yellow]…[/]"}
COLOUR = {"ok": "green", "error": "red", "rejected": "red", "deferred": "yellow", "repeat": "dim"}


def who(by: dict[str, Any] | None) -> str:
    return f"{by['who']} via {by['via']}" if by else "nobody"


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
        elif t == "validation":
            self.c.print(f"  [{'green' if d.get('ok') else 'red'}]validator[/] {esc(json.dumps(d, ensure_ascii=False))}")
        elif t == "report":
            self._report(d["report"], d.get("status"))
        elif t == "run_paused":
            self.c.rule("paused at a gate")
            self.c.print(f"resume with: {esc(d['resume'])}")
        elif t == "run_finished":
            self.c.rule(esc(str(d["status"])))
        elif t == "error":
            self.c.print(f"[red]error[/] {esc(json.dumps(d, default=str, ensure_ascii=False))}")

    def _report(self, r: dict[str, Any], status: str | None) -> None:
        self.c.print(Panel(esc(r["summary"]), title=f"report as of {esc(str(r['as_of']))} ({esc(str(status))})"))
        table = Table(show_header=True, header_style="bold")
        for column in ("id", "role", "status", "conf", "statement", "support", "against"):
            table.add_column(column)
        for h in r["hypotheses"]:
            table.add_row(esc(h["id"]), esc(h["role"]), esc(h["status"]), f"{h['confidence']:.2f}", esc(h["statement"]),
                          esc("\n".join(h["support"])), esc("\n".join(h["against"])))
        self.c.print(table)
        for a in r.get("actions", []):
            self.c.print(f"  {a['rank']}. {esc(a['action'])} [dim]({esc(a['owner'])}; {esc(', '.join(a['evidence']))})[/]")
        for row in r.get("do_not", []):
            self.c.print(f"  [red]do not[/] {esc(row['action'])}: {esc(row['why'])}")
        for p in r.get("proposed_actions", []):
            self.c.print(f"  proposal {esc(p['tool'])} {esc(json.dumps(p['args'], ensure_ascii=False))}: [bold]{p['decision']}[/] by {who(p['by'])}"
                         + (f" ({esc(str(p['reason']))})" if p.get("reason") else ""))
        for q in r.get("open_questions", []):
            self.c.print(f"  [dim]open: {esc(q)}[/]")


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
            answer = Prompt.ask(esc("[a]pprove, [r]eject or [d]efer"), choices=["a", "r", "d"], default="a", console=c)
            if answer == "a":
                out[card["call_id"]] = {"approve": True}
            elif answer == "d":
                out[card["call_id"]] = {"defer": True}
            else:
                out[card["call_id"]] = {"approve": False, "reason": Prompt.ask("reason (goes back to the agent)", console=c)}
        return out

    return decide
