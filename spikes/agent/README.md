# Agent spike

A throwaway spike. It proves the framework for the Smalls investigator, not the agent itself. The design and the options it tests are in `docs/research/agent-design.md`.

## What it proves

- A LangGraph `StateGraph` with explicit nodes: `orient -> agent -> gate -> [ask] -> tools -> agent ... -> finalize`.
- A model chosen by config (`config.toml` profiles, or `SMALLS_PROFILE` and `SMALLS_MODEL`) through `init_chat_model`.
- Two read tools and one mock action:
  - `search_logs` and `get_record` are stubs with PLAN's signatures, the A1 envelope and the A2 error contract, run over a tiny synthetic fixture. smalls/tools had no tools on this branch yet.
  - `propose_rollback` touches nothing real.
  - Two internal tools: `update_hypotheses` (the board in state) and `submit_report` (the validator).
- An approval gate in two nodes: `gate` decides the policy once, `ask` only calls `interrupt()`:
  - Approve, reject with a reason (it goes back to the agent as an error ToolMessage), or defer. Headless runs defer.
  - Global auto-approve is on by default; `--interactive` turns it off; `--headless defer|approve|reject` answers gates without asking.
  - Action tools gate even when auto-approve is on. That is a proposal, and an open question for Rhys.
- Thinking and every tool stage stream live to the terminal through one typed event protocol (`spike/events.py`). The same events go to `events.jsonl` and are SSE-ready.
- A SQLite checkpointer. A run paused at a gate resumes in a new process.
- `now` is injected through the LangGraph runtime context and is never a tool parameter. A log line after `now` is invisible.
- One run record per run, in `runs/<run_id>/`: `run.json`, `events.jsonl`, `calls.jsonl`, `report.json`, `usage.json`, `summary.json`, `checkpoints.sqlite`. `sample-run/` is a committed example: a gate, a rejection, and a re-plan.

## Run it

No key is needed; the default profile is a scripted, deterministic model.

```
cd spikes/agent
uv sync
uv run python -m spike run                     # auto-approve reads; the gated action asks at a terminal, else defers
uv run python -m spike run --interactive       # approve or reject every gated call in the terminal
uv run python -m spike run --run-id demo --stop-at-gate
uv run python -m spike resume demo --reject "a rollback was already tried and failed"
uv run python -m spike replay sample-run       # re-render a run record: no model, no key
uv run python -m spike history demo            # checkpoints, for rewind or fork
uv run python -m spike run --bad-handle        # the validator rejects a fabricated handle, one retry
uv run python -m spike bench                   # framework overheads
uv run --group dev pytest -q                   # 19 offline tests
```

With a key: `ANTHROPIC_API_KEY=... uv run python -m spike run --profile anthropic`. This live path is untested here because this environment has no key. The offline test `test_anthropic_payload_keeps_thinking_and_batches_tool_results` builds the exact request without sending it.

The question is deliberately trivial and has nothing to do with the development incident.
