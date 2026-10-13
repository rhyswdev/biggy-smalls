"""Terminal rendering of the event protocol, and the interactive gate."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markup import escape as esc
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .events import Event


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
            self.c.rule(f"run {ev.run_id}")
            self.c.print(f"now {d['now']}  model {d['model']}  auto-approve {'on' if d['auto_approve'] else 'off'}")
            self.c.print(f"[bold]Q:[/] {esc(d['question'])}")
        elif t == "phase":
            self.c.print(f"[cyan]── {d['name']}[/]")
        elif t == "text_delta":
            self.c.print(esc(d["text"]), end="")
        elif t == "model_call":
            u = d.get("usage", {})
            self.c.print(f"[dim]model call {d['latency_ms']} ms · in {u.get('input_tokens')} out "
                         f"{u.get('output_tokens')} · ${d.get('cost_usd', 0):.4f} · {d.get('stop_reason')}[/]")
        elif t == "tool_call_proposed":
            args = json.dumps(d["args"], ensure_ascii=False)
            self.c.print(f"[yellow]▶ {d['tool']}[/] [dim]{d['kind']}[/] {esc(args[:160])}")
            if d.get("rationale"):
                self.c.print(f"  [dim]why: {esc(d['rationale'])}[/]")
        elif t == "gate_pending":
            self.c.print(f"[magenta]⏸ gate: {len(d['calls'])} call(s) need approval[/]")
        elif t == "gate_decision":
            mark = {"approved": "[green]✓[/]", "rejected": "[red]✗[/]", "deferred": "[yellow]…[/]"}[d["decision"]]
            extra = f": {esc(d['reason'])}" if d.get("reason") else ""
            wait = f" after {d['wait_ms']} ms" if d.get("wait_ms") is not None else ""
            if d["by"] != "policy" or d["decision"] == "rejected":
                self.c.print(f"  {mark} {d['tool']} {d['decision']} by {d['by']}{wait}{extra}")
        elif t == "tool_result":
            col = {"ok": "green", "error": "red", "rejected": "red", "deferred": "yellow"}[d["status"]]
            self.c.print(f"  [{col}]← {d['tool']} {d['status']}[/] {len(d['handles'])} handles, {d['chars']} chars, "
                         f"{d['latency_ms']} ms" + (f"  [dim]{esc(d['native_query'])}[/]" if d.get("native_query") else ""))
        elif t == "hypothesis_update":
            self.c.print(f"  [bold]board[/] {d['id']} {d['status']} {d['confidence']:.2f}: {esc(d['statement'])}")
        elif t == "budget":
            self.c.print(f"  [dim]budget {d['used']}/{d['limit']}[/]")
        elif t == "validation":
            self.c.print(f"  [{'green' if d['ok'] else 'red'}]validator[/] {esc(json.dumps(d))}")
        elif t == "report":
            r = d["report"]
            tbl = Table(show_header=True, header_style="bold")
            tbl.add_column("hypothesis")
            tbl.add_column("conf")
            tbl.add_column("support")
            for h in r["hypotheses"]:
                tbl.add_row(esc(h["statement"]), f"{h['confidence']:.2f}", esc("\n".join(h["support"])))
            self.c.print(Panel(esc(r["summary"]), title=f"report as of {r['as_of']}"))
            self.c.print(tbl)
            for i, a in enumerate(r["actions"], 1):
                self.c.print(f"  {i}. {esc(a)}")
            for p in r.get("proposed_actions", []):
                self.c.print(f"  proposal {p['tool']} {esc(json.dumps(p['args']))}: [bold]{p['state']}[/]"
                             + (f" by {p['by']}" if p.get("by") else "") + (f" ({esc(p['reason'])})" if p.get("reason") else ""))
        elif t == "run_paused":
            self.c.rule("paused at a gate")
            self.c.print(f"resume with: {d['resume']}")
        elif t == "run_finished":
            self.c.rule(f"{d['status']}")
            if d.get("resume"):
                self.c.print(f"resume with: {d['resume']}")
        elif t == "error":
            self.c.print(f"[red]error[/] {esc(str(d))}")


def interactive_decider(console: Console | None = None):
    c = console or Console(highlight=False)

    def decide(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out = {}
        for x in calls:
            c.print(Panel(f"[bold]{x['tool']}[/] ({x['kind']})\n{esc(json.dumps(x['args'], indent=1))}\n\n"
                          f"why: {esc(x.get('rationale') or '(none given)')}", title="approve this call?"))
            ans = Prompt.ask(esc("[a]pprove or [r]eject"), choices=["a", "r"], default="a", console=c)
            if ans == "a":
                out[x["call_id"]] = {"approve": True}
            else:
                out[x["call_id"]] = {"approve": False, "reason": Prompt.ask("reason (goes back to the agent)", console=c)}
        return out
    return decide
