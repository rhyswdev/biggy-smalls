# Build plan: the tool layer and the agent

Ruling record and build plan, written on 2026-10-13 on branch `agent-build` (worktree `.claude/worktrees/agent-build`, cut from `f056377`). It covers everything between ingestion and a frozen agent: the eleven tools (PLAN section 6 rows 6a-tools, 6b and the tool part of step 7), the run records (6c) and the agent (build rows 1 to 8 of `docs/research/agent-design.md` section 5, with the fixes in its section 8.4).

It assumes step 6a landed exactly as `docs/decisions/ingestion-decisions.md` specifies. Section 9 lists every assumption to check when the two branches meet.

A fresh session should be able to build from this file plus `docs/research/agent-design.md` and PLAN sections 3, 4, 6 and 13. This record reopens no ruling. Where it orders work differently from PLAN section 6 or agent-design section 5, it wins on order. It contains no incident content and no held-out content, so it is safe reading for the prompt author (section 3).

## 1. Rulings (2026-10-13)

| # | Point | Ruling | Why | Requirement served |
|---|---|---|---|---|
| G1 | Scope | One track builds all eleven tools and the agent. | The tools are the agent's tools: their descriptions are prompt text, their envelope feeds the validator, and the agent's done criteria need all eleven. Splitting them left the first run waiting on an unplanned step. | "Focus on the AI agent first"; "Tool use" |
| G2 | Compose | The baseline stands. The agent runs inside the existing app service; no new service in this step; `uv` on the host stays the primary path. An API or UI service joins `compose.yaml` with the UI step. | A gate is a stop and a resume, which the CLI already does across processes. A server adds a resume race and gate expiry that only a web client needs. | "Instructions to run the system locally" |
| G3 | Report schema | Adopt both open review points: `subject` on every hypothesis, and an `alerts[]` list with a verdict per alert stream. | `subject` makes "what it blames" one checkable field. The alert verdicts are the signal-versus-noise answer the brief asks for, and give the UI a table. | "Identify meaningful signals from noisy evidence" |
| G4 | Prompt authoring | Blind first prompt. Prompt v1 and every tool description are written by a session that has not read the incident (section 3). | The method in agent-design section 2.0 was written without sight of the incident. The first prompt should be provably the same, so git shows a clean starting point before any tuning. | "Grounded reasoning based on evidence" |
| G5 | Tags | Every tool is built and tested with tags present as a first-class path. A missing tag carries no meaning: the classifier applies a tag only at high confidence, so untagged is the normal outcome of doubt and may be a model miss. | A facet that silently hid unlabelled rows would turn a classifier gap into missing evidence. | "Good handling of ambiguity and uncertainty" |

G5 does not change ruling O2: `SMALLS_TAGS` still defaults to `off`. See open point P1.

## 2. In and out

**In.** The registry and the result envelope; the eleven tools with their tag inputs; the source inventory; the values derived at `now`; alias and edge derivation for `get_topology`; `smalls tools call`; the tool-output half of the leak test; the report schema; the agent package (graph, state, gates, events, runner, run record, validator, verifier, carry-forward); the terminal renderer; the matrix runner with arms A and C; the retrieval check; process metrics.

**Out, with its owner.**

| Work | Owner | Starts when |
|---|---|---|
| Scorer, answer key schema, the three hand-written reports, the claim-support judge and its prompt | A scorer session. Agent sessions do not edit the scorer, and the judge prompt is held out from them. | `smalls/contracts/report.py` and the run-record format are committed (build rows 1.1 and 1.2) |
| Trap matching on `subject`; the manifest field behind the alert check | Scorer and data sessions | With the scorer |
| Gate B, the blind read-through with only the CLI | A fresh session | Build row 3.4 |
| Contrarian control, framing perturbations, alias shuffle, arms B, D and E | PLAN step 10 | After `v1-submittable` work begins |
| FastAPI and SSE, the web UI, `report.html`, the resume compare-and-set and durable gate expiry that a web client needs | The UI step | After build row 4.2 |
| Vector spikes, held-out runs, the write-up | PLAN steps 8b, 11, 12 | As PLAN orders them |

## 3. Session rules

**Held-out rule.** Every session on this track writes the agent or `smalls/tools/`, so it never reads or lists `eval/heldout/`, `scenario/variants/` or `bundles/h*/`. Repo-wide searches exclude those paths.

**No answer material in agent-side code.** Nothing under `smalls/` imports `smalls_data` or opens `_internal/` or `scenario/`. Tests may read `_internal/manifest.jsonl` for expected values. Tool code, descriptions and prompts are never shaped by the manifest's `class`, `sig_id` or `kind`, or by the answer key.

**Blind first prompt (G4).** We split what the model reads into two kinds.

| Kind | What | Written by |
|---|---|---|
| Instructions and descriptions | The system prompt; the first-turn template; the verifier prompts; the description of every tool, every parameter and every report field; the vocabulary of the action tool | The prompt author, blind |
| Mechanical output | Row layouts, footers, error-contract sentences, the board status line, rejection and deferral messages | The build session, from the wording PLAN section 4 and agent-design give |

Instructions and descriptions live in a few text files, so authorship is one `git log` away and the lint has one place to scan:

- `smalls/agent/prompts/investigator.md`, `first_turn.md`, `verifier_claims.md`, `verifier_contrarian.md`
- `smalls/tools/descriptions.yaml`: tool, parameter and report-field descriptions; the argument models read them at import

Until the blind pass lands, these files hold placeholder text marked `PLACEHOLDER`. Offline tests use a scripted model and do not care. A live run refuses to start while any placeholder remains.

The prompt author's reading list is closed:

- `docs/requirements.md`
- `docs/research/agent-design.md`
- PLAN section 4 (the tool surface) and section 13 (acceptance)
- this record
- the output of `smalls agent schema`, which prints every tool and report schema with structure only
- the spike's prompt in `spikes/agent/spike/graph.py`, as prompt v0

Not on the list: PLAN sections 1, 2 and 3 (their examples carry incident identifiers), `docs/HANDOFF.md`, `docs/DATA.md`, anything under `bundles/`, `scenario/` or `eval/runs/`, and tool output from the development bundle. The author commits the prose files alone, in a commit whose message names the reading list.

Rules the author follows: every rule is generic and would be true on another incident; no entity, service, person or vendor-specific heuristic; tools are described by what they do, since the first-turn inventory names what exists; the bundle-string lint must pass.

**After v1.** A prompt change is driven by run records, so it is tuned on the development incident. That is expected, and it is what the held-out runs are for. Each change is one commit that names the run id that motivated it and states the generic failure it fixes.

## 4. Two tracks, one repository

Ingestion runs in the main checkout on `step6a-ingestion`. This track runs in the worktree. `.claude/worktrees/` is listed in `.git/info/exclude`, which is local and untracked, so the main checkout never sees it.

Until the branches meet, this track creates new files only, with two exceptions.

| Path | Owner until the merge |
|---|---|
| `smalls/tools/store.py`, `smalls/tools/ingest/**`, `smalls/cli.py`, `Dockerfile`, `compose.yaml`, `.dockerignore`, `Makefile`, `tags/`, `eval/tags/`, `docs/HANDOFF.md`, `docs/PLAN.md`, `docs/DATA.md`, `CLAUDE.md` | Ingestion |
| `smalls/agent/**`, `smalls/contracts/envelope.py`, `smalls/contracts/report.py`, `smalls/tools/registry.py`, `smalls/tools/schemas.py`, `smalls/tools/descriptions.yaml`, `tests/agent/**`, this record | This track |
| `pyproject.toml`, `uv.lock` | Both. We add the agent dependencies now and accept a mechanical conflict: take theirs, re-add our lines, run `uv lock`. |

Doc edits to shared files wait for the merge and are listed in section 12. Until then the agent's commands run as `python -m smalls.agent`; they move under the `smalls` command in row 2.0.

The worktree has its own `.venv`. It has no `.env`: live runs load keys with `uv run --env-file` pointing at the main checkout's file, which is git-ignored and never copied.

## 5. Contracts, built first

These unblock everything else, including the scorer session.

**Result envelope** (`smalls/contracts/envelope.py`; ruling A1 with the split of agent-design ruling 11).

```python
@dataclass(frozen=True)
class Row:
    handle: str
    text: str                       # this handle's slice of the text, as the model saw it

@dataclass(frozen=True)
class Envelope:
    text: str                       # what the model reads
    rows: tuple[Row, ...] = ()      # one per handle whose content is in the text
    referenced: tuple[str, ...] = ()# handles only listed as children or pointers
    native_query: str | None = None
    window: tuple[str, str] | None = None   # ISO UTC, after clamping
    status: Literal["ok", "error"] = "ok"
    meta: Mapping[str, Any] = MappingProxyType({})  # total, truncated, clamped, sampled, tag-filter counts

    @property
    def shown(self) -> tuple[str, ...]: ...   # the handles of rows
```

`shown[]` is derived from `rows`, so "citable only if its content was shown" holds by construction. `rows` also feeds `evidence.jsonl` (agent-design 8.2) without a second fetch. This adds one field to A1 and removes none (judgment call J9).

**Registry** (`smalls/tools/registry.py`).

```python
@dataclass(frozen=True)
class ToolContext:                  # injected by the harness, never a tool parameter
    store: Store                    # opened, clock already set to now
    now: datetime
    tags: bool                      # SMALLS_TAGS
    run_id: str | None = None

@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["read", "action", "internal"]
    args: type[BaseModel]           # the source of the model-facing schema
    fn: Callable[[BaseModel, ToolContext], Envelope]
    cap_tokens: int
    mock: bool = False              # true for every action tool in v1

def registry(*, tags: bool) -> dict[str, ToolSpec]: ...   # fixed order; tag inputs exist only when tags is true
def call(spec: ToolSpec, raw_args: dict, ctx: ToolContext) -> Envelope: ...
```

`call` is the one path every caller uses: the agent, the sweep, the CLI and the leak test. It validates arguments, runs the tool, applies the cap and converts any exception into the A2 error text, so it never raises. The agent receives a registry by injection, which is how tests and build phase 1 run it over a fixture registry.

The argument models for all eleven tools are written in phase 1, in `smalls/tools/schemas.py`, from the inputs PLAN section 4 lists: names, types, enums and bounds, with no implementation behind them. That gives the prompt author every schema at once, and it fixes the cached prefix before the first live run.

The tool schemas differ with tags on and off, so each mode has its own cached prefix, and `run.json` records the tool-schema hash and the flag.

**Report** (`smalls/contracts/report.py`, with `report.schema.json` generated from it and committed). PLAN section 3.1 as written, plus G3:

- `hypotheses[].subject`: the one entity the hypothesis blames; `entities` still lists everything it touches.
- `alerts[]`: `{handle, verdict: this_incident | other_incident | noise}`.
- `hypotheses[].source`: `stated | derived`, carried from the board (agent-design section 3).

`proposed_actions[]` is written by code and is not part of what the model submits.

**Internal tools** (`smalls/agent/internal_tools.py`, kind `internal`, never gated, every write validated).

- `update_board(hypotheses?, timeline?, open_questions?)`. A hypothesis update carries only the fields the model sets: `id`, `statement`, `role`, `status`, `source`, `subject`, `entities`, `changes`, `confidence`, `support[]`, `against[]`, `because[]`, `discriminating[]`.
- `submit_report(...)`: the report minus the code-written fields. It must be the only call in its turn.

**Action tool** (`smalls/agent/actions.py`, kind `action`, `mock=True`). One tool, `propose_action(kind, target, params?, evidence[], expect)`, with a closed `kind` list. It touches nothing and returns a line saying the proposal was recorded for the incident commander. See open point P2.

## 6. The tool layer

### 6.1 Rules for every tool

PLAN section 4 fixes the surface, the inputs and the caps. These are the build rules beneath it.

1. A tool reads only the `visible` views, through `ToolContext.store`. A test fails if tool code names a base table.
2. `since` and `until` accept ISO times or a relative form such as `-30m`, resolve against `now`, and are clamped to it. The result says when a window was clamped.
3. Every row starts with its handle. Times are UTC, with the native value beside it where it differs.
4. Caps count tokens as characters over 4. Rows drop from the end until the result fits. A truncated result ends with a footer giving the total and how to narrow. The native query echo comes last.
5. A handle goes in `rows` only if the text includes that record's own content. A bare id, a child or a pointer goes in `referenced`.
6. Errors follow A2 and come from one module: an unknown handle and a not-yet-visible handle get the same answer; an empty result says how to widen; a missing record says "not in this bundle".
7. A value derived at `now` (Datadog monitor state, PagerDuty service status, Slack thread summaries, SolarWinds active state, Dynatrace evidence count, the metric envelope) comes from `smalls/tools/derive.py`, never from a served field that ingestion withheld.
8. Tools run one at a time. They are in-memory queries that take microseconds, and one SQLite connection is not shared across threads.

### 6.2 Tags (G5)

Rules T1 to T10 stand. These are the consequences for tools and for the agent.

- A tag filter is never a default, and the opening sweep never uses one.
- A row shows its tag as a machine label when it has one, and shows nothing when it has none.
- A tag-filtered result ends with a footer: how many rows matched without the filter, and how many of those are untagged. A tag-filtered result with no rows says the same, so "nothing tagged" is never read as "nothing there".
- `group_by=signal` prints an `untagged` row with its count. Untagged rows are never dropped and never folded into `other` or `routine`.
- Each tag input accepts the value `untagged`, so the rows a filter hid are one call away (open point P3).
- An aggregate that depends on a tag (a tag filter or a tag grouping in stats mode) mints no `q:` handle and says so. A tag is never evidence, so a count built from tags cannot be cited. The rows themselves stay citable, however they were found (judgment call J11).
- The registry offers only the facets ingestion marked as meeting the precision bar.
- The source inventory gives counts per facet over visible rows, with the untagged count beside them.
- The prompt author's brief carries the rule in plain words: a tag is a search aid, a missing tag says nothing about relevance, and a tag-filtered search is followed by a look at what it left out before anything is called absent.
- `calls.jsonl` records whether a call used a tag input, so the ablation can report how often tags were used and what followed.

### 6.3 Per tool

"Golden" means a test whose expected values are derived from the manifest at test time.

| Tool | Build notes beyond PLAN section 4 | `rows` / `referenced` | Tag input | Accepted when |
|---|---|---|---|---|
| `search_logs` | Terms are ANDed with `instr`. Rows mode folds traces to 3 frames. Stats mode groups by minute, host or level and mints a `q:` handle. `group_by=template` reads the stored `template_id` and prints the top templates with an "other" row. Every count from a sampled source is labelled from `keep_1_in`. | Log records / none | `signal`, `group_by=signal` | Golden on first row and total per filter; sampled label present; no class name in any output |
| `list_alerts` | One row per `stream_key` with first seen, last change, state at `now` and a transition count. Flaps collapse to one row. Splunk deliveries collapse by `sid`. | Alert streams / linked PagerDuty incident, saved search | `signal` | A flapping stream is one row; a duplicate delivery is one row with `deliveries: 2` and both handles resolving |
| `list_changes` | Builds and change requests side by side, ordered by start, never joined or ranked. | Changes and builds / commits | `change_kind` | Golden on the set in a window; a building version shows no result or duration |
| `read_chat` | Mentions resolved from the `ref` table. Reply count recomputed from visible replies. | Messages / unexpanded replies | `act` | Reply count at each checkpoint equals the visible replies; a withheld field never appears |
| `get_record` | The version valid at `now`. Empty fields hidden and counted. Text clipped at 2k characters. A saved search returns its definition and the visible alerts it fired, and is never run. | The record / its children | none | Each versioned source returns the version the manifest says is valid at each checkpoint |
| `search_knowledge` | FTS5 `bm25()`; every token quoted; tokens joined with OR. We re-measure the finding that an AND join returned nothing for 9 of 12 queries. A closed incident returns its whole close note. | Hits / none | none | No query text can raise a syntax error; the retrieval check runs |
| `query_metrics` | At most 4 series and 45 points, nulls kept. Summary with min, max, first breach and null runs. The handle comes from `metric_handle()` with the window clamped (A3). | The windowed metric handle / none | none | No point at or after `now`; the envelope carries no withheld end time |
| `check_status_page` | The latest snapshot published before `now`. With no provider, it lists the known pages. | The snapshot / none | none | Each checkpoint returns the snapshot valid then |
| `itsm_query` | `table` is an enum. Filters become `json_extract` on `native`. Echoes `sysparm_query`. The live incident is the version valid at `now`. | Rows / journal entries | `signal` on incidents | Golden per table; display values match the valid version |
| `get_oncall` | Escalation levels, who is on call at `now`, the escalation timeout. | Services, policies, on-call rows / none | none | Golden at each checkpoint |
| `get_topology` | Alias candidates with their match method; non-matches stay visibly unresolved. Edges come from the CMDB, Dynatrace entities and Datadog `peer.service`, each with its source, built from visible rows at query time. Up to 2 hops by a recursive CTE. The owner's active flag is shown. | CIs, relationships, entities / none | none | A two-hop traversal returns each edge once with its source; every resolved alias is correct; recall is printed, not asserted |

`smalls/tools/inventory.py` builds the source inventory (R1) from visible rows: knowledge documents, metric series and scopes, saved searches with fired counts, chat channels, log sourcetypes and hosts, time coverage per source and, when tags are on, facet counts.

`smalls tools call <name> --now <time> key=value ...` prints the envelope text, then the shown and referenced handles. `smalls tools list` and `smalls tools schema` print the surface.

## 7. The agent

### 7.0 Framework

The framework is settled in agent-design sections 1.1 and 2, and this plan builds on it unchanged: a custom LangGraph `StateGraph` with explicit nodes, and models reached through LangChain so the provider is a config value. Not the Claude Agent SDK, which works with one provider and whose state is a transcript; not a hand-written loop, which lacks a resume in another process, crash recovery, typed reducers and one multiplexed stream.

Pins, carried from the spike, with minor-version caps because the event stream and delta channels are still moving:

| Package | Pin | Resolved in the worktree | Used for |
|---|---|---|---|
| `langgraph` | `>=1.2,<1.3` | 1.2.15 | The graph, reducers, interrupts, streaming |
| `langgraph-checkpoint-sqlite` | `>=3.1,<3.2` | 3.1.2 | `SqliteSaver`, one checkpoint database per run |
| `langchain` | `>=1.4,<1.5` | 1.4.5 | `init_chat_model` |
| `langchain-core` | `>=1.6,<1.7` | 1.6.10 | Messages, `content_blocks`, the scripted chat model |
| `langchain-anthropic` | `>=1.7,<1.8` | 1.7.6 | The default provider |
| `langchain-openai` | `>=1.7,<1.8`, optional group | not installed | The cross-provider verifier profile |
| `rich` | `>=13` | 15.0.0 | The terminal |

Where each piece is used:

| Framework feature | Where | Note from the design |
|---|---|---|
| `StateGraph(InvestigationState, context_schema=RunContext)` with plain-function nodes | `graph.py`: orient, sweep, agent, gate, ask, tools, verify, finalize | Axis 1, option (b) |
| Reducers: `add_messages`, a field-wise board merge, `operator.add` | `state.py` | `messages` is append-only; a custom reducer must be an inspectable callable |
| Runtime context (`Runtime[RunContext]`) for `now`, run id, policy flags and budgets | every node | LangGraph does not checkpoint it, so `run.json` keeps it and a resume passes it again |
| `interrupt()` and `Command(resume={interrupt_id: {call_id: decision}})` | the `ask` node and the runner | The policy lives in `gate`, which never re-runs; `ask` only interrupts. Resume by id: a bare value fails when several interrupts are pending |
| `SqliteSaver` | `runner.py` | Default durability for attended runs; `durability="exit"` for matrix runs |
| `stream(..., stream_mode=["messages", "updates", "custom"], version="v2")` and `get_stream_writer()` | `runner.py` only | The one module that knows stream modes; everything downstream sees our events |
| `RetryPolicy` and an explicit `timeout=` | model nodes | LangGraph retries nothing and LangChain sets no timeout by default |
| `GraphRecursionError` | caught in `runner.py` | The runner renders the partial report from state |
| `init_chat_model` behind our adapter | `models.py` | Per-provider kwargs, not `configurable_fields`, which leaks one provider's defaults into another's request |
| `bind_tools` with our per-provider schema adapter | `models.py` | `bind_tools` drops `strict=True` for dict schemas |
| `content_blocks` | `models.py` | Maps each provider's reasoning to one block type |
| `ToolMessage(status="error")` | the `tools` node | Rejections and tool errors, in the same user message as the batch's other results |
| `usage_metadata` | the `agent` node, into state | Usage survives a pause or a crash |

Deliberately not used: `create_agent` with `HumanInTheLoopMiddleware` and `ToolStrategy`; `with_structured_output` or a forced tool call for the report; `SummarizationMiddleware` and provider-side context editing; `astream_events`; the beta v3 event stream; `DeltaChannel`, which is the production path for checkpoint size but still beta. LangSmith stays off unless its environment variable is set.

### 7.1 Layout

```
smalls/agent/
  config.py  config.toml   profiles, budgets, policy flags, prices; the only place a model name appears
  models.py                per-provider kwargs and schema adapter; reasoning normalisation; the scripted model
  state.py                 InvestigationState and its reducers
  board.py                 board validation, confidence caps, status line, ready and stale checks (pure)
  gate.py                  the policy (pure) and the decision vocabulary
  graph.py                 orient, sweep, agent, gate, ask, tools, verify, finalize
  internal_tools.py        update_board, submit_report
  actions.py               the mock action tool
  validator.py             citation and structure checks for reports and board writes
  verifier.py              packets, claim support, contrarian, revision rules
  carry.py                 board renderer, carry-forward, restart from the board
  events.py                the event protocol, one seq writer, isolated sinks, tolerant reader
  record.py                the run record files and the runs index
  runner.py                drives the graph, maps stream parts to events, resumes
  render.py                the terminal: header, cards, citations, the interactive decider
  prompts/                 blind-authored text
eval/
  matrix.py                arms, checkpoints, repetitions; one process per run
  baseline.py              arm A
  retrieval/               the retrieval check queries and its runner
  runs/<run_id>/           run records
tests/agent/  tests/tools/  offline; a fixture registry ported from the spike
```

The agent takes the trigger as text and `now` as a time. Resolving a checkpoint to a trigger file is the job of `eval/` and the Makefile, so nothing under `smalls/` names `triggers/`.

One process holds one store and one clock. A resume in a new process rebuilds the store, which is deterministic, and sets the clock from `run.json`. The matrix runner starts one process per run, so two checkpoints can never share a clock.

### 7.2 Where the design needed a concrete form

Agent-design fixes the method. These are the judgment calls made to turn it into code. Each is ours to overrule.

| # | Design text | Concrete form |
|---|---|---|
| J1 | "The board is typed state"; the model writes judgements | `update_board` carries hypotheses, timeline rows and open questions. `source` is set when a hypothesis is created and cannot change. |
| J2 | "Ready" when the leading hypothesis has non-chat support for each link and one discriminating check, and every rival has `against` | The model lists in `discriminating[]` the handles it claims would exist only if the hypothesis were true. Code signals ready when: one hypothesis is `leading`; it has a non-chat handle in `support` and in `discriminating`; every other hypothesis that is not rejected and has role `trigger` or `contributing` has non-chat support; and every other hypothesis with role `cause` has an `against` handle. The status line says ready, or names what is missing. |
| J3 | "Classed with chat" | `handle_class()` in `smalls/contracts/handles.py` is the one definition, shared with the scorer. `slack:` and `ic:note/` handles are chat. See open point P4. |
| J4 | "A repeated identical call returns the earlier result" | Identity is the tool name plus canonical argument JSON, without `rationale`. The repeat gets a one-line pointer to the earlier round, not the text again, and costs nothing. |
| J5 | "The budget is checked per call" | Reads and actions count. Internal calls, sweep calls and repeats do not. A revision grants five more. Model turns are capped at the budget plus 6. |
| J6 | "`inconclusive` is a legitimate status" | A submitted report with no `leading` hypothesis is inconclusive. It is valid when it has at least one open question. No schema field is added. |
| J7 | The alert verdict list (G3) | The validator wants one verdict for every alert stream the sweep showed. A report that misses some gets one retry with the list; after that, code records the rest as `unassessed`. |
| J8 | "Its cited rows are re-served this run" | Record handles are re-fetched with `get_record` and entered in the ledger as `carry_forward`. Aggregate and windowed metric handles change with `now`, so their calls are re-run from the earlier run's `calls.jsonl` and the seeded board cites the new handle. A handle that no longer resolves is dropped from the seed and listed. |
| J9 | The A1 envelope | `rows[]`, as in section 5. |
| J10 | "Reads the incident channel" | The channel is an input, `--channel`. Without it, the sweep reads the newest messages across all channels up to the cap. Sweep windows are config values, recorded in `run.json`. |
| J11 | "A tag is a query facet, never evidence" | Tag-dependent aggregates mint no citable handle (section 6.2). |

Starting values, all in `config.toml` and re-set after the first measured run: 30 gated calls; stale after 3 rounds without a board change; stuck after 3 empty results; restart from the board when measured input plus `max_tokens` passes 85 percent of the model's input limit; the verifier on.

### 7.3 Provider facts to confirm in the live shakedown

Each is asserted in agent-design from the provider reference. The shakedown confirms it against a live call and records the number or the outcome in agent-design section 6.

1. Reasoning summaries stream as text when asked for; between-tool notes arrive as reasoning, so visible text deltas are nearly empty.
2. Forced tool choice is rejected, so `submit_report` is always the model's choice under `auto`.
3. Strict tool schemas pass through our adapter; numeric bounds are stripped and enforced by our own validation.
4. The cache-read ratio is above zero from the second call, with effort fixed for the run.
5. Reasoning blocks replay unchanged and the history is never edited; the append-only test holds on a live transcript.
6. Parallel results return in one user message, and a rejection arrives as an error result beside them.
7. A refusal stop reason is handled: server-side fallback if the client library passes it through, else the fallback profile once.
8. Usage fields price correctly, including the split cache-write counters.
9. An explicit timeout is set, and long outputs stream.
10. No sampling parameter is sent.

Two options to try, neither required: streaming the report tool's input as it is generated, so the draft report appears early; and sending harness text (the status line, notes) through the provider's operator channel where one exists, so it is not mixed with tool output.

## 8. Build order and acceptance

Phase 1 needs no store and starts now. Phase 2 starts the moment ingestion lands and preempts whatever is left of phase 1: the first real run is worth more than any row still queued. Unfinished phase 1 rows move after phase 2, in the order shown.

Live runs spend money. None starts without our go-ahead, and the matrix is sized after the first measured run.

### Phase 1: now, beside ingestion

| # | Build | Accepted when |
|---|---|---|
| 1.1 | Contracts: the envelope, the registry types, the argument models for all eleven tools, `handle_class()`, the report model with G3 and its generated JSON Schema | A full example report validates; the schema file matches the model; `python -m smalls.agent schema` prints every tool and report schema; the scorer session can start |
| 1.2 | Port the LangGraph spike to `smalls/agent/` (agent-design row 1), on the pins in section 7.0: config, models, state, graph, gate, events, runner, record, with the registry injected. With it: the stable `stream(version="v2")` parts, caching on by default, the runtime context guard, headless approve-and-record with the mock guard, the validator checking shown handles, `ic:note` handles, `evidence.jsonl` written at call time | The spike's 19 tests pass against the port, the bundle-string and model-name tests among them; a scripted run over the fixture registry writes a complete run record; a replay renders it with no key |
| 1.3 | Live plumbing shakedown on the fixture registry with prompt v0 (the first half of row 2) | Section 7.3 is ticked off; latency, cost and cache-read ratio per call replace "not measured" for the plumbing |
| 1.4 | The method in code (rows 3 and 4): `board.py`, the sweep node (it runs whichever of its three tools the registry holds), `update_board`, `submit_report` with the full schema, `proposed_actions` from the ledger, `do_not`, `stakeholder_update`, the provider schema adapter, `propose_action` | A unit test per cap and per status-line flag; zero unknown or referenced-only citations survive the validator; a report with a missing alert verdict gets one retry |
| 1.5 | Failure handling (row 5 and section 8.4): one row of agent-design axis 8 at a time; records written in `finally`; `error` and `run_finished` on every exit; tolerant reader; isolated sinks; coalesced reasoning deltas; server-minted run ids; the runs index; `deferred`; `ic_note` | One scripted test per row of axis 8; an injected provider timeout leaves a full record and a resumable checkpoint; no file in a run directory contains a key value |
| 1.6 | The blind prompt pass (G4): prompt v1, the verifier prompts, every description | The placeholder gate clears; the bundle-string lint passes; the cached prefix size is reported |
| 1.7 | The terminal (the CLI part of the UI row): sticky header from the board, one card per round, numbered citations, `show <handle>`, heartbeat | A paused run resumes from another process and renders the same; every tool string is rendered as text |
| 1.8 | Verifier (rows 7a and 7b) over scripted models: code checks, packets from `evidence.jsonl`, the uncited-evidence digest, claim support, the contrarian pass, the revision rules, the changed-claims re-check, the flag, both model profiles | Verdicts recorded; a scripted unsupported claim triggers exactly one revision; a contrarian check already in the ledger triggers none |
| 1.9 | Carry-forward (row 8): the board renderer, re-served rows, the gap-handle check, restart from the board | A second-checkpoint run seeds from the first; a carried leading hypothesis with no handle known after the earlier `as_of` is flagged |

### Phase 2: when ingestion lands, to the first end-to-end run

| # | Build | Accepted when |
|---|---|---|
| 2.0 | Rebase onto the landed branch; work through section 9; apply the shared-file edits of sections 10 and 12 | `make data`, `make lint` and `make test` pass on the rebased branch; every difference from the ingestion record is written into section 9 |
| 2.1 | Tool plumbing: `call`, windows, rendering and caps, the error module, `derive.py`, the base-table test, the golden test harness | An unknown tool, bad arguments and a raised exception each come back as A2 text |
| 2.2 | `search_logs`, rows and stats | Its row in 6.3. The agent runs over the real registry and writes a record: the done condition of agent-design row 1 |
| 2.3 | `list_alerts`, then `list_changes` | Their rows in 6.3 |
| 2.4 | `read_chat`, `get_record`, `smalls tools call` | Their rows in 6.3 |
| 2.5 | The tool-output half of the leak test at both checkpoints, with the derived-state assertions listed in HANDOFF; `make tools-smoke` | `make leak-test` covers the store and every tool built so far, and grows with each later tool |
| 2.6 | The sweep wired to the real tools; prompt v1 in place; a minimal inventory (log sourcetypes and hosts, chat channels) until row 3.1 | **First end-to-end live run at the second checkpoint** writes a complete run record |
| 2.7 | Live shakedown on real tools (the second half of row 2): budgets set from measurements | Real numbers in agent-design section 6; the matrix cost is known before any matrix runs |

### Phase 3: the rest of the tools, beside agent iteration

| # | Build | Accepted when |
|---|---|---|
| 3.1 | `search_knowledge` and the source inventory | Their rows in 6.3; the inventory replaces the fixture inventory in the first turn |
| 3.2 | `query_metrics`, `check_status_page` | Their rows in 6.3 |
| 3.3 | `search_logs group_by=template` | The top-N cut and the "other" row; counts labelled when sampled |
| 3.4 | A scripted solve path of about 15 CLI calls, kept under `eval/`, never near a prompt | The path reaches the cause. **Gate B** is then run by a fresh session with only the CLI. If the cause cannot be found, we fix the bundle, not the agent. |
| 3.5 | Run records and the matrix (6c): `eval/matrix.py`, arm C, arm A in `eval/baseline.py`, process metrics in `summary.json` | One command runs arm C at the second checkpoint three times; that is the regression set from here on |
| 3.6 | `itsm_query`, `get_oncall`, `get_topology` with alias and edge derivation | Their rows in 6.3; the leak test covers all eleven tools |
| 3.7 | The retrieval check: about 10 queries phrased as an incident commander would ask them | Recall@1 and MRR reported as a sanity check |
| 3.8 | The tags pass over every tool: each tag input, the footers, the `untagged` bucket, inventory counts, the tag flag as a matrix dimension | Section 6.2 holds for every tool with tags on; with tags off, every tool output is byte-identical to before this row |

### Phase 4: after the first scored run

These need the scorer, which is another session's.

| # | Build | Accepted when |
|---|---|---|
| 4.1 | Whatever is left of rows 1.8 and 1.9 | Their acceptance |
| 4.2 | The keep-or-kill rule for the verifier, on the regression set, for both profiles | The rule in agent-design axis 5 is evaluated and the result recorded |
| 4.3 | Carry-forward from the first checkpoint to the second, live | The continuity check passes |
| 4.4 | The tags ablation: the regression set with the flag off and on | The numbers PLAN section 6.1 names are reported, whichever way they point |
| 4.5 | Docs: HANDOFF, agent-design section 6, the edits of section 12 | A fresh session can start the next step from HANDOFF alone |

**If time runs short**, cut in this order: the contrarian pass and its revision (1.8, second half); `ic_note`; the `untagged` filter value. Never cut the leak test, the validator, the run record or replay.

### Done means

- From a clean clone with a key, one command investigates the bundle at the second checkpoint and writes a schema-valid report with zero fabricated, unseen or future citations. With no key, one command replays a committed run.
- The leading hypothesis cites a non-chat handle for each link; each rejected hypothesis states what contradicts it; the report lists what not to do; the owner comes from on-call data; every alert stream the sweep showed has a verdict.
- The agent stops on its own inside its budget, and a run that degrades says so: `incomplete`, dropped citations, policy rejections and `stuck` events are all counted.
- A run paused at a gate resumes in a new process. A rejected action reaches the model with its reason and appears in `proposed_actions` as written by code.
- The leak test passes for the store and all eleven tools at both checkpoints, with tags off and with tags on.
- With tags off, no tool output mentions a tag. With tags on, no result hides an untagged row without saying how many.
- Every prompt and description was first written blind, and git shows it. No prompt, description or agent-side string contains a bundle string; no model name appears outside `config.toml`.
- The regression set runs with one command, and every run writes a complete record, failed runs included.
- The carry-forward run from the first checkpoint cites at least one handle known only in the gap.

## 9. Assumptions to check at the merge

We built against the ingestion record as written. Each line is checked against the landed code in row 2.0, and any difference is recorded here with its effect.

1. How a tool gets a store and how the clock is set; that `visible`, `visible_point` and `visible_chunk` exist; that a `ref` table holds Slack users and channels.
2. The columns tools read: `stream_key`, `parent`, `template_id`, `keep_1_in`, `sample_per`, `tags`, `attrs`, `flags`, `native`, `t_native`, `tz_rule`, `entity_raw`.
3. The one function that renders `native` without withheld fields.
4. The `dataset` and `kind` values per source, which `itsm_query` and `list_alerts` map onto.
5. Tags: how `SMALLS_TAGS` is read; the shape of the `tags` column; which facets are offered; how bot posts are labelled.
6. The CLI: how subcommands are added; how the bundle path is given; how a clock time such as `14:52` resolves to a full time.
7. The path guard: whether it covers only the loader, since `eval/` reads trigger files.
8. The bundle-string lint: where it lives and how its file list is extended to the prompt and description files.
9. The manifest row count, and any DATA entry that changed a record shape.
10. The measured ingest time, since every run and every resume rebuilds the store.

## 10. Compose and running (G2)

No service is added. These are the edits to shared files, made in row 2.0.

- `pyproject.toml`: `langgraph`, `langgraph-checkpoint-sqlite`, `langchain`, `langchain-core`, `langchain-anthropic` and `rich` join `dependencies` at the pins in section 7.0. `langchain-openai` goes in an optional group, since only the cross-provider verifier profile needs it.
- `Dockerfile`: sync that optional group too.
- `compose.yaml`: pass `SMALLS_TAGS`, `SMALLS_PROFILE`, `SMALLS_MODEL` and `SMALLS_COMMIT` through by name. Take the image tag from an environment variable, so two checkouts never overwrite each other's image.
- `.dockerignore` and `.gitignore`: `.claude/`, checkpoint databases and the runs index.
- `Makefile`: `run`, `resume`, `demo` (a replay), `tools-smoke`, `regress`, `matrix`.

Things that differ inside the container:

- `docker compose run` gives a terminal, so gates ask; `-T` or `--headless` makes a run unattended.
- Run records land on the bind mount. If the checkpoint database is slow there, `SMALLS_STATE_DIR` moves it; we test a pause in one container and a resume in another.
- The image has no git, and a worktree's `.git` points at a host path. `run.json` takes the commit from `SMALLS_COMMIT`, which the Makefile sets, then from git, then records "unknown".
- Keys reach the container only through the `env_file` at run time. A run record stores the names of the variables that were set, never their values, and row 1.5 scans each run directory for key values.

## 11. Open points

| # | Point | Default the build uses |
|---|---|---|
| P1 | Should `SMALLS_TAGS` default to `on`? Ruling O2 says off, with `on` to be ruled from the ablation numbers. | Off. Both modes are built and tested, so the switch is one config value. |
| P2 | The action surface: one `propose_action` with a closed `kind` list, or several typed tools. | One tool. Its `kind` list and descriptions are written in the blind pass from standard incident-response vocabulary, so the list is not shaped by this incident. |
| P3 | Accept `untagged` as a value of each tag input. PLAN lists the inputs but not this value. | Accept it. It adds a way to reach rows and removes none. |
| P4 | Are human-written journal entries and PagerDuty notes classed with chat? PLAN names only `ic:note` beside chat. | No: only `slack:` and `ic:note/`. The scorer session owns the final answer, through `handle_class()`. |
| P5 | Which run records are committed. | The runs the write-up cites, without their checkpoint databases. |
| P6 | A mechanical guard for the held-out rule in this worktree: a sparse checkout that leaves the three paths off disk, or deny rules in the session settings. | None yet; the rule is kept by discipline. |

## 12. Edits to other documents, applied at the merge

- **PLAN 3.1**: `subject`, `alerts[]` and `source`; "inconclusive" defined as no leading hypothesis.
- **PLAN 3.2**: `handle_class()` as the one definition of the chat class.
- **PLAN 4**: `rows[]` in the envelope; tag-dependent aggregates are not citable; the `untagged` bucket and value, if P3 is ruled in.
- **PLAN 6**: the status line; this record as the order for 6a-tools through step 7.
- **PLAN 13**: a pointer to this record.
- **HANDOFF**: the two review points are ruled (G3), with the scorer and data halves still open; status and next step.
- **CLAUDE.md**: this record in the doc map; the blind-prompt rule; the new make targets.
- **agent-design section 6**: measured numbers after rows 1.3 and 2.7.
