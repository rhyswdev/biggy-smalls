# Build plan: the tool layer and the agent

Ruling record and build plan, written on 2026-10-13 on branch `agent-build` (worktree `.claude/worktrees/agent-build`, cut from `f056377`). It covers everything between ingestion and a frozen agent: the eleven tools (PLAN section 6 rows 6a-tools, 6b and the tool part of step 7), the run records (6c) and the agent (build rows 1 to 8 of `docs/research/agent-design.md` section 5, with the fixes in its section 8.4).

Two things happened after the first draft, and both are folded in. Two independent reviews read it as an execution plan, one for the tools and one for the agent; their accepted findings are in the text and the reviews themselves are not kept. And step 6a landed on `step6a-ingestion`: we read the store as built at `df733de` and replaced our assumptions about it with what is there (section 6.1).

A fresh session should be able to build from this file plus `docs/research/agent-design.md` and PLAN sections 3.1, 3.2, 3.4, 4, 5, 6 and 13. This record reopens no ruling. Where it orders work differently from PLAN section 6 or agent-design section 5, it wins on order. It contains no incident content and no held-out content, so it is safe reading for the prompt author (section 3).

## 1. Rulings (2026-10-13)

| # | Point | Ruling | Why | Requirement served |
|---|---|---|---|---|
| G1 | Scope | One track builds all eleven tools and the agent. | The tools are the agent's tools: their descriptions are prompt text, their envelope feeds the validator, and the agent's done criteria need all eleven. | "Focus on the AI agent first"; "Tool use" |
| G2 | Compose | The baseline stands. The agent runs inside the existing app service; no new service in this step; `uv` on the host stays the primary path. An API or UI service joins `compose.yaml` with the UI step. | A gate is a stop and a resume, which the CLI already does across processes. A server adds a resume race and gate expiry that only a web client needs. | "Instructions to run the system locally" |
| G3 | Report schema | Adopt both open review points: `subject` on every hypothesis, and an `alerts[]` list with a verdict per alert stream. | `subject` makes "what it blames" one checkable field. The alert verdicts are the signal-versus-noise answer the brief asks for, and give the UI a table. | "Identify meaningful signals from noisy evidence" |
| G4 | Prompt authoring | Blind first prompt. Prompt v1 and every tool description are written by a session that cannot see the incident (section 3). | The method in agent-design section 2.0 was written without sight of the incident. The first prompt should be provably the same, so git shows a clean starting point before any tuning. | "Grounded reasoning based on evidence" |
| G5 | Tags | Every tool is built and tested with tags present as a first-class path. A missing tag carries no meaning: the classifier applies a tag only at high confidence, so untagged is the normal outcome of doubt and may be a model miss. | Fewer than half the taggable units carry a tag at the measured floor. A facet that silently hid unlabelled rows would turn a classifier gap into missing evidence. | "Good handling of ambiguity and uncertainty" |
| G6 | Tags default | `SMALLS_TAGS` defaults to `on`, for now. This replaces ruling O2, ahead of the ablation numbers O2 was waiting for. The ablation still runs both arms, and turning the default off again would be a ruling made on those numbers. | Tags are built, measured at a floor where every applied tag on the sample matched its label, and guarded by G5, so the agent should be developed with the surface it will ship with. | "Make intentional decisions and be prepared to explain them" |
| G7 | No text is lost | Nothing a record holds is unreachable. A list view may shorten a row, but it says so and names the call that returns the whole record. `get_record` returns every character of a record, in parts when the record is larger than the cap. This replaces PLAN section 4's "long text clipped at 2k chars". | A clip with no way to read past it can drop the one sentence that mattered, and no tool would show that anything was missing. Paging keeps the per-call cap, which protects the context, and loses nothing. | "Explain its reasoning using evidence from the provided inputs" |

G6 has two consequences outside this track. The default lives in `store.tags_enabled()`, and the ingestion tests and four documents state the old one, so the flip is coordinated with the ingestion session (section 9, item 8). And ruling O4 deferred the tagging of held-out bundles on the grounds that held-out runs would use the default, off. With the default on, O4 has to be settled before a held-out run (open point P8).

## 2. In and out

**In.** The registry and the result envelope; the eleven tools with their tag inputs; the source inventory; the values derived at `now`; alias and edge derivation for `get_topology`; `smalls tools call`; the tool-output half of the leak test; the report schema; the agent package (graph, state, gates, events, runner, run record, validator, verifier, carry-forward); the terminal renderer; the matrix runner with arms A and C; the retrieval check; process metrics.

**Out, with its owner.**

| Work | Owner | Starts when |
|---|---|---|
| Scorer, answer key schema, the three hand-written reports, the claim-support judge and its prompt | A scorer session. Agent sessions do not edit the scorer, and the judge prompt is held out from them. | Rows A1 and A2 commit the report schema and the run-record format |
| Trap matching on `subject`; the manifest field behind the alert check | Scorer and data sessions | With the scorer |
| Gate B, the blind read-through with only the CLI | A fresh session | Row D5 |
| Contrarian control, framing perturbations, alias shuffle, arms B, D and E | PLAN step 10 | After the regression set is stable |
| FastAPI and SSE, the web UI, `report.html`, and the resume compare-and-set and durable gate expiry that a web client needs | The UI step | After row E3 |
| Vector spikes, held-out runs, the write-up | PLAN steps 8b, 11, 12 | As PLAN orders them |

## 3. Session rules

**Held-out rule.** Every session on this track writes the agent or `smalls/tools/`, so it never reads or lists `eval/heldout/`, `scenario/variants/` or `bundles/h*/`. Repo-wide searches exclude those paths.

**No answer material in agent-side code.** Nothing under `smalls/` imports `smalls_data` or opens `_internal/` or `scenario/`. Tests may read `_internal/manifest.jsonl` for a record's handle, path, locator and times, and compute the rest from served bytes; they never select by the manifest's `class`, `sig_id` or `kind`. Tool code, descriptions and prompts are never shaped by those fields or by the answer key. A build session reads PLAN sections 3.1, 3.2, 3.4, 4, 5, 6 and 13; it has no need of sections 1, 2 or 3.3 and does not read them.

**Blind first prompt (G4).** We split what the model reads into two kinds.

| Kind | What | Written by |
|---|---|---|
| Instructions and descriptions | The system prompt; the first-turn template; the verifier prompts; the description of every tool, every parameter and every report field; the list and wording of action kinds | The prompt author, blind |
| Mechanical output | Row layouts, footers, error sentences, the board status line, rejection and deferral messages, the sweep message | The build session, from the wording this record and agent-design give |

Instructions and descriptions live in five files, so authorship is one `git log` away and the lint has one place to scan:

- `smalls/agent/prompts/investigator.md`, `first_turn.md`, `verifier_claims.md`, `verifier_contrarian.md`
- `smalls/tools/descriptions.yaml`: tool, parameter, report-field and action-kind text; the argument models read it at import

Until the blind pass lands, these files hold placeholder text marked `PLACEHOLDER`. Offline tests use a scripted model and do not care. A live run refuses to start while any placeholder remains.

**The blind room.** A reading list in a prompt cannot be enforced, and a session started in the repository is shown the project instructions, recent commit subjects and the memory index before it reads a word. So the author does not work in the repository. Row C2 runs `python -m smalls.agent blind-room <dir>`, which writes a folder outside the repository holding exactly:

- `requirements.md` and `agent-design.md`, copied
- this record, copied
- `schema.json`: every tool and report schema with tags on, structure only, placeholders where descriptions go
- `prompt-v0.txt`: the system prompt string from the spike
- `BRIEF.md`: the author's instructions, and the slots the first-turn template must hold (`{now}`, `{inventory}`, `{trigger}`, `{budget}`, `{prior_board}`)
- the five target files, holding placeholders

A fresh session is started in that folder and writes the five files. It has no repository to wander into. The build session then copies them in, runs the placeholder gate, the slot check and the bundle-string lint, and commits them alone, with the room's file list in the message. If the lint finds a hit, the author fixes it in the room; the build session does not edit the prose.

Everything the author needs that PLAN would have given is restated in this record without incident identifiers: what each tool reads and returns (section 6.7) and what each report field means (section 5). PLAN itself is not in the room, because its examples carry the incident's identifiers.

Rules the author follows: every rule is generic and would be true on another incident; no entity, service, person or vendor-specific heuristic; tools are described by what they do, since the first-turn inventory names what exists; a tag is a search aid and a missing tag says nothing about relevance.

**After v1.** A prompt change is driven by run records, so it is tuned on the development incident. That is expected, and it is what the held-out runs are for. Each change is one commit that names the run id that motivated it and states the generic failure it fixes.

## 4. Where the work happens

This track runs in the worktree, on `agent-build`. `.claude/worktrees/` is listed in `.git/info/exclude`, which is local and untracked, so the main checkout never sees it.

Ingestion is built on `step6a-ingestion` and is not yet on `main`. Row A0 rebases this branch onto it. The ingestion session is still running, on tagging experiments, so that branch will keep moving: this track does not edit `smalls/tools/ingest/tagging/`, `tags/` or `eval/tags/`, and it rebases again whenever that branch moves, which is cheap because the two no longer touch the same files. Everything else under `smalls/tools/` is this track's from row A0 on.

`pyproject.toml` and `uv.lock` take the agent dependencies in row A2. If the ingestion branch moves after the rebase, the conflict is mechanical: take theirs, re-add our lines, run `uv lock`.

The worktree has its own `.venv`. It has no `.env`: live runs load keys with `uv run --env-file` pointing at the main checkout's file, which is git-ignored and never copied.

Commits stay local on `agent-build` until we decide to push; for this track that overrides "push often" in the project instructions.

## 5. Contracts, built first

These unblock everything else, including the scorer session.

**Result envelope** (`smalls/contracts/envelope.py`). This supersedes the `{text, handles[], native_query, window}` shape of ruling A1 and carries the split of agent-design ruling 11.

```python
@dataclass(frozen=True)
class Row:
    handle: str
    text: str                       # this handle's slice of the text, as the model saw it
    t_known: str | None = None      # ISO UTC from the store; None for an aggregate or a note

@dataclass(frozen=True)
class Envelope:
    text: str                       # what the model reads
    rows: tuple[Row, ...] = ()      # one per handle whose content is in the text
    referenced: tuple[str, ...] = ()# handles only listed as children or pointers
    native_query: str | None = None
    window: tuple[str, str] | None = None   # ISO UTC, after clamping
    status: Literal["ok", "error"] = "ok"
    meta: Mapping[str, Any] = MappingProxyType({})

    @property
    def shown(self) -> tuple[str, ...]: ...   # the handles of rows
```

`shown[]` is derived from `rows`, so "citable only if its content was shown" holds by construction. `rows` also feeds `evidence.jsonl` without a second fetch, and `t_known` is what the carry-forward check reads, since nothing under `smalls/` may open the manifest. `meta` always carries `total`, `returned`, `truncated`, `clamped`, `empty`, `sampled` and `tag`.

**Registry** (`smalls/tools/registry.py`).

```python
@dataclass(frozen=True)
class ToolContext:                  # injected by the harness, never a tool parameter
    store: Store                    # smalls.tools.store.Store, clock already set to now
    now: datetime
    tags: bool                      # store.tags_enabled()
    run_id: str | None = None

@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Literal["read", "action", "internal"]
    args: type[BaseModel]           # the source of the model-facing schema
    fn: Callable[[BaseModel, ToolContext], Envelope]
    cap_tokens: int                 # PLAN's token figure, or 6,000 where PLAN gives only a row count
    cap_rows: int | None = None     # PLAN's row, message, edge or cell limit
    mock: bool = False              # true for every action tool in v1

def registry(*, tags: bool) -> dict[str, ToolSpec]: ...   # fixed order; tag inputs exist only when tags is true
def call(spec: ToolSpec, raw_args: dict, ctx: ToolContext) -> Envelope: ...
```

`call` is the one path every caller uses: the agent, the sweep, the CLI and the leak test. It validates arguments, runs the tool, checks the result against its caps and converts any exception into the A2 error text, so it never raises.

A tool does not build its own envelope. It returns parts: header lines, rows in order, footer hints and the native query. One shared renderer turns the parts into the envelope and applies both limits, so a row cut from the text is also cut from `rows`.

A tool that is not built yet is absent from the registry, so the agent never sees it. The registry grows through phases B and D, and `run.json` records the tool-schema hash of each run. The agent receives a registry by injection, which is how offline tests run it over a fixture registry (section 7.4).

**Argument models** (`smalls/tools/schemas.py`). All eleven are written in row A1, with no implementation behind them, so the prompt author sees every schema at once and the cached prefix is fixed before the first live run. PLAN section 4 names the inputs but leaves defaults, types and enum values open; section 6.5, K1 fixes them. Every read and action schema also carries a required `rationale: {tests, expect}` from one base model (agent-design ruling 8): the hypothesis the call tests, and what the model expects to see. The tools node strips it before `call`. Internal tools carry none.

A strict schema cannot hold an object with open keys: the provider's schema transform turns one into an empty object. So a free-form input is always a list of `{name, value}` string pairs.

**Report** (`smalls/contracts/report.py`, with `report.schema.json` generated from it and committed). PLAN section 3.1, plus G3. The generated schema carries no description text, so the blind pass never changes the contract the scorer reads.

| Field | Meaning | Written by |
|---|---|---|
| `as_of` | The run's `now` | code |
| `summary` | What is happening, in a few sentences, with handles inline | model |
| `impact` | `business_service`, `cis[]`, `priority {suggested, impact, urgency}`, `assignment_group`, `oncall`, `customer`; null in a partial report | model |
| `hypotheses[]` | `id` (stable, reused across updates and checkpoints), `statement`, `role` (`cause`, `trigger`, `contributing`, `symptom`, `unrelated`), `status` (`leading`, `open`, `rejected`), `subject` (the one entity it blames), `entities[]` (everything it touches), `changes[]` (handles of change records it implicates), `confidence`, `support[]`, `against[]` | model |
| `hypotheses[].source`, `.history` | `stated` or `derived`; one row per checkpoint, `{as_of, status, confidence, because[]}` | code, from the board |
| `timeline[]` | `t_utc`, `event`, `evidence[]` | model |
| `actions[]` | `rank`, `action`, `owner`, `evidence[]`: the ranked recommendation | model |
| `do_not[]` | `action`, `why`, `evidence[]` | model |
| `alerts[]` | `handle`, `verdict`: `this_incident`, `other_incident` or `noise` from the model; code adds `unassessed` | model, then code |
| `open_questions[]` | What is still unknown | model |
| `stakeholder_update` | `status`, `customer_impact`, `scope`, `changed_since_last`, `next_steps`, `next_update_at`; null in a partial report | model |
| `proposed_actions[]` | One row per gated action call with its gate decision | code, from the ledger |

There is no top-level `trigger`: it is the hypothesis with that role, and it may be absent. The hypotheses in `submit_report` are applied to the board as a last update, under the same validation and caps, and the report's hypotheses are then read from the board, so the two cannot disagree; an `id` the board does not hold is a validation error. The board keeps a history row per change, `{round, call_id, from, to, because[]}`, in `board.json`; the report's `history` is the per-checkpoint view of it.

**Internal tools** (`smalls/agent/internal_tools.py`, kind `internal`, never gated, every write validated).

- `update_board(hypotheses?, timeline?, open_questions?)`. A hypothesis update carries only the fields the model sets: the model-written fields above, plus `source`, `because[]` and `discriminating[]`.
- `submit_report(...)`: the model-written fields above. It must be the only call in its turn.

**Action tool** (`smalls/agent/actions.py`, kind `action`, `mock=True`). One tool, `propose_action(kind, target, params[], evidence[])`, where `params` is a list of name and value pairs and what the model expects comes from `rationale.expect`. It touches nothing and returns a line saying the proposal was recorded for the incident commander. The `kind` list is written in the blind pass; until then it holds one placeholder entry. See open point P2.

## 6. The tool layer

### 6.1 What the landed store gives us

Read at `df733de`. Row A0 confirms each line still holds.

- `store.build(bundle, tags=None)` returns a `Store`. `Store.set_now(...)` takes epoch milliseconds, an aware datetime, an ISO time, or a clock time such as `14:52` on the bundle's day; `clear_now()` unsets it. The build takes about 215 ms, so every run and every resume can rebuild.
- A tool may read `visible`, `visible_point`, `visible_chunk`, `ref` and `tag_answer`. `visible_chunk` exposes `chunk_id`, which joins it to `chunk_fts`. An existing test fails any module under `smalls/`, other than the store, that names `record`, `point` or `chunk` in SQL.
- `native` is shown only through `store.render_native(native, flags)`, for `ref` rows too. A tool never prints `valid_to`, or a `t_end` at or after `now`.
- `ref` rows have no clock. They hold the chat users and channels files and the metric response envelopes.
- Columns: `kind` and `dataset` values are in `docs/DATA.md` 100, `sev` in 101, a log's `title` and `body` in 103, `parent` and `stream_key` in 108. `attrs` holds parsed vendor fields and is queried with JSON1. A wall-clock time in `attrs` or `native` is converted with `store.integration.parse(...)` or `.local(...)` before it is shown as UTC.
- `parent` already links, inside one source: a journal entry and an audit row to their incident, a paging log entry to its incident, a commit to its build, a chat reply to its thread root, a monitor event to its monitor, an alert delivery to its saved search. Nothing is resolved across sources, and `entity_id` is empty.
- `stream_key` already groups an alert stream per source.
- `point.handle` is the series identity `metric:<query>`, and `point.series` is the series expression, so a grouped query keeps its series apart.
- The full-text table holds page sections, close notes of incidents and journal entries, each hanging off its own record version, so a note is searchable exactly when it is visible.
- Logs: terms are matched against `native` as well as `body`. A template is shown as `masker.mask(title)` of one of its rows. `keep_1_in` is the rate and `sample_per` the unit; no column holds a sampling class name.
- Tags: with tags on, `tags` is `{facet: value}`. The floor is measured and sits in the tagging config. All three facets met the precision bar and are offered. About half the taggable units carry no tag.
- The store half of the leak test is `tests/test_leak.py`. It has the time scanner, the checkpoint list, the future tokens and the two exemptions for scheduled times.
- The bundle-string lint is `smalls_data/prompt_lint.py`; `prompt_texts()` lists what it scans.

### 6.2 Rules for every tool

PLAN section 4 fixes the surface, the inputs and the caps. These are the build rules beneath it.

1. A tool reads only what 6.1 allows, by way of `ToolContext.store`. `chunk_fts` is read only joined to `visible_chunk`. No tool needs `tag_answer`.
2. `since` and `until` resolve against `now` and are clamped to it. The result says when a window was clamped.
3. Every row starts with its handle. Times are UTC, with the native value beside it where it differs.
4. Caps count tokens as characters over 4. Results are ordered by time, oldest first, and rows drop from the end until the result fits. Two exceptions: `read_chat` keeps the newest messages and drops the oldest, and `search_knowledge` is ordered by rank. A truncated result ends with a footer giving the total, the range that was cut and how to narrow. The native query echo comes last.
5. A handle goes in `rows` only if the text includes that record's own content. A bare id, a child or a pointer goes in `referenced`. K7 settles the cases this does not decide.
6. Errors follow A2 and come from one module, with one fixed sentence per case (K8).
7. A value derived at `now` comes from `smalls/tools/derive.py`, never from a served field that ingestion withheld. K4 gives the rule and the tool for each.
8. Tools run one at a time. They are in-memory queries that take microseconds, and one SQLite connection is not shared across threads.
9. Nothing semantic happens in a tool: no causal link, no ranking of changes, no parsing of pasted tables. A link between two records is printed only when a served field states it, never from a matching title or time.
10. Because `ref` rows have no clock, anything listed from one is filtered through visible rows first: the channel list is the channels with visible messages, and a metric is listed only when it has a visible point. A channel's member list or count is never printed.
11. Nothing a record holds is unreachable (G7). A list view may shorten a row: a log line to 300 characters, a trace to 3 frames, a knowledge hit to a snippet. Each shortened row is marked, and `get_record` on its handle returns the whole record. The only things a tool leaves out for good are empty fields, which it counts, and fields ingestion withheld because they are not true at `now`.

### 6.3 Tags (G5)

Rules T1 to T10 stand. These are the consequences for tools and for the agent.

- A tag input is built with its tool, in that tool's row, and tested against the committed tag cache. Row D9 is the acceptance across all of them.
- A tag filter is never a default, and the opening sweep never uses one.
- A row shows its tag in one fixed form, `[tag <facet>=<value>]`, when it has one, and shows nothing when it has none. With tags off that form never appears, and a test asserts it.
- A collapsed row, such as an alert stream or a log template, shows the distinct tags of its records and matches a filter if any of them does.
- The registry builds each tag input's enum from the tagging profiles, so the tools follow the taxonomy if it changes: the facet's options without `other`, plus `untagged` (open point P3). `act` also has `automated`, the value code gives bot posts.
- "Untagged" counts the rows of the kinds a facet applies to that carry no value for it. A kind the facet does not apply to is not counted.
- A tag-filtered result ends with a footer: how many rows matched without the filter, and how many of those are untagged. A tag-filtered result with no rows says the same, so "nothing tagged" is never read as "nothing there".
- `group_by=signal` prints an `untagged` row with its count. Untagged rows are never dropped and never folded into `other` or `routine`.
- An aggregate that depends on a tag (a tag filter or a tag grouping in stats mode) mints no `q:` handle and says so. A tag is never evidence, so a count built from tags cannot be cited. The rows themselves stay citable, however they were found.
- A facet that stops meeting the precision bar is dropped from the registry: no input, no row label, no inventory count. Today all three are offered.
- The source inventory gives counts per facet over visible rows, with the untagged count beside them.
- The prompt author's brief carries the rule in plain words: a tag is a search aid, a missing tag says nothing about relevance, and a tag-filtered search is followed by a look at what it left out before anything is called absent.
- `calls.jsonl` records whether a call used a tag input, so the ablation can report how often tags were used and what followed.
- Agent runs use tags on, the default (G6), from the first live run. The regression set runs at the default, and the ablation in row E5 compares it with tags off.

### 6.4 Per tool

| Tool | Build notes beyond PLAN section 4 | `rows` / `referenced` | Tag input | Accepted when |
|---|---|---|---|---|
| `search_logs` | Terms are ANDed with `instr` over `native` and `body`. Rows mode folds traces to 3 frames. Stats mode mints a `q:` handle; its table shape and the sampled estimate are in K5. `group_by=template` groups on `template_id` and prints the top templates with an "other" row. | Log records / none. Stats: one row, the `q:` handle | `signal`, `group_by=signal` | Golden on first row and total per filter; sampled label present; no sampling class name in any output; the newest buckets are never cut |
| `list_alerts` | One row per `stream_key` with first seen, last change, state at `now` and a transition count, all computed over the stream's whole visible history. Flaps collapse to one row. Duplicate deliveries collapse by `sid`. The row's handle and each source's state rule are in K2. | The stream's earliest record / its later records, its definition record, a linked paging incident, its saved search | `signal` | A flapping stream is one row; a duplicate delivery is one row with `deliveries: 2` and both handles resolving; a stream's handle is the same at both checkpoints |
| `list_changes` | Builds and change requests side by side, ordered by start, never joined or ranked. Listed when its span overlaps the window; a change that has not started is marked `planned` (K6). A change request shows its served state; there is no rewind. | Changes and builds / commits | `change_kind` | Golden on the set in a window; a building version shows no result or duration; a planned change is listed, not lost |
| `read_chat` | Mentions resolved from the users `ref` file. Reply count and repliers recomputed from visible replies through `parent`. | Messages / unexpanded replies | `act` | Reply count at each checkpoint equals the visible replies; a withheld field never appears; a channel with no visible message is not listed |
| `get_record` | The version valid at `now`. Empty fields hidden and counted. No field is clipped (G7): a record that renders larger than the cap is split at line ends into parts, the result says "part 1 of n", and `part` asks for another. Children come from `parent`: an incident's journal entries and audit rows, a build's commits, a thread's replies. A saved search returns its definition and the visible alerts it fired, and is never run. | The record, in every part / its children | none | Each versioned source returns the version the manifest says is valid at each checkpoint; for every visible record, its parts joined hold every non-empty field it may show, in full |
| `search_knowledge` | FTS5 `bm25()` over `chunk_fts` joined to `visible_chunk`; every token quoted; tokens joined with OR. We re-measure the finding that an AND join returned nothing for 9 of 12 queries and record the result in DATA. A closed incident returns its whole close note. | Hits; two hits on one page are two rows with one handle / none | none | No query text can raise a syntax error; a close note is found by a word that appears only in it; the retrieval check runs |
| `query_metrics` | At most 4 series and 45 points, nulls kept. How `metric` and `scope` select a served query, what `rollup` does and how first breach is found are in K3. Summary with min, max, first breach and null runs. The handle comes from `metric_handle()` with the served query string and the clamped window (A3). | The windowed metric handle, one row per call / a monitor whose threshold was used | none | No point at or after `now`; the envelope's end is the last visible point; two served queries that differ only in aggregator are told apart |
| `check_status_page` | The latest snapshot published before `now`. With no provider, it lists the known pages. | The snapshot / in listing mode, every page | none | Each checkpoint returns the snapshot valid then |
| `itsm_query` | `table` is `incident`, `journal`, `user` or `group`. Filters are equality on the display value, by `json_extract`. Every row prints its number or name, short description and state whatever `fields` says, so every row is content. Newest first. Echoes the native query. The live incident is the version valid at `now`. | Rows / journal entries | `signal` on incidents | Golden per table; display values match the valid version |
| `get_oncall` | Escalation levels, who is on call at `now`, the escalation timeout. | Services, policies, on-call rows / none | none | Golden at each checkpoint |
| `get_topology` | Alias candidates with their match method; non-matches stay visibly unresolved. Edges come from the CMDB, monitored entities and service-call tags, each with its source, built from visible rows at query time; no table is written. Up to 2 hops by a recursive CTE, ordered by hop, then source. The owner's active flag is shown. Methods and edge rules are in K9. | CIs, relationships, entities; an edge's row is the record that states it / other records stating the same edge | none | A two-hop traversal returns each edge once with its source; every resolved alias is correct; recall is printed, not asserted |

`smalls/tools/inventory.py` builds the source inventory (R1) from visible rows: knowledge documents, metric series and scopes, saved searches with fired counts, chat channels, log sourcetypes and hosts, first and last visible record time per source and, when tags are on, facet counts. It is capped at 2k tokens: past 20 entries of a kind it prints the count and the first 20. Every handle it lists goes in `referenced`.

`smalls tools call <name> --now <time> key=value ...` prints the envelope text, then the shown and referenced handles. A value is read as JSON when it parses as JSON, so a list or the filter pairs can be written inline. `smalls tools list` and `smalls tools schema` print the surface.

### 6.5 Where PLAN section 4 needed a concrete form

These are the calls made to turn the surface into argument models and behaviour. Each is ours to overrule.

**K1. Argument conventions.**

- Times: `since` is required; `until` is optional and defaults to `now`. Accepted forms: ISO-8601 with `Z` or an offset (a bare ISO time is UTC); `-<n><s|m|h|d>`, relative to `now`; the word `now`. `since` at or after `until` is an argument error.
- A value that comes from the bundle (`sourcetype`, `host`, `channel`, `provider`, `service`, `policy`, `entity`, `metric`, `scope`) is a free string checked at run time against visible rows, never an enum. An unknown value answers with the values that exist. This keeps the schemas the same for every bundle.
- `text` on any tool is a case-insensitive substring of the row's title and body.
- `search_logs`: `terms` is 0 to 8 strings; `group_by` is `minute`, `host`, `level` or `template`; `bucket` is `1m` or `5m`, default `1m`, used only with `group_by=minute`; `limit` is 1 to 50, default 30, rows mode only.
- `list_alerts`: `source` is a free string; `state` is `active` or `all`, default `all`.
- `itsm_query`: `filters` is at most 6 `{name, value}` pairs, ANDed; `fields` is at most 12 names; `limit` is 1 to 20, default 20.
- `search_knowledge`: `query` is 1 to 300 characters; `k` is 1 to 5, default 5.
- `get_oncall`: `service` and `policy` are optional strings (a name, an id or a handle). Exactly one is given; with neither, the tool lists the visible services and policies.
- `get_topology`: `hops` is 1 or 2, default 1.
- `get_record`: `part` is an optional integer from 1, default 1. A part past the last answers with how many parts there are.
- `read_chat`: `channel` omitted means every channel with a visible message; `thread_ts` is the `ts` as printed; `text` also matches replies, and a matching reply is its own row.

**K2. An alert stream's handle and state.** A stream row's handle is the handle of the stream's earliest visible record. The visible set only grows, so the handle is the same at every checkpoint, and it is the handle `alerts[]` uses. That record's title and time are printed, so it is in `rows`; the stream's other records are listed on the row and go in `referenced`. A stream is listed when one of its records falls in the window or it is active at any point in it. State: a monitor stream takes its last visible transition; a versioned problem or paging incident takes the version valid at `now`; a network-monitor alert is active from its trigger until its `t_end`; a search alert has no state and is shown as `fired` under both `state` values. A monitor with no visible transition is not a row, and the footer counts them. A paging incident is linked to a stream only when a served field of the incident names the stream's source record.

**K3. Selecting a metric.** `metric` is the aggregator and metric name as the inventory prints them. `scope` is a comma-separated tag filter, compared as a set with the served query's filter. The pair must select exactly one served query, and its group series are returned in tag order, at most 4, the rest named in the footer with their maxima. A `scope` that adds one group tag selects that one series. No match answers "not in this bundle" and lists the served queries for that metric name. `rollup` is `avg`, `max`, `min` or `sum`: the function that merges native points into at most 45 buckets. Its default is the query's own aggregator, with a percentile treated as `max`. First breach: for each visible monitor whose query is exactly this served query, the first native point in the window that crosses its critical threshold in the monitor's own direction, and the same for its warning threshold; with no such monitor the summary says so. When a window's ends fall on different dates the text prints both dates. The handle still carries only the two clock times, so two such windows may share a handle, which we accept because a metric handle resolves by its query.

**K4. Values derived at `now`.** Each has a test at both checkpoints that computes the expected value from visible rows.

| Value | Rule | Shown by |
|---|---|---|
| Monitor state | The last visible transition per group; the overall state is the worst group. With none, it prints "no transition seen" and claims no state | `list_alerts`, `get_record` |
| Paging service status | `critical` if a valid incident version is triggered; `warning` if one is acknowledged and none is triggered; otherwise `active` | `get_oncall`, `get_record` |
| Thread summary | Count, repliers and latest reply, from visible replies | `read_chat`, `get_record` |
| Network-monitor alert active | Triggered before `now` and `t_end` empty or not before `now`. A later reset or acknowledgement time is not printed | `list_alerts`, `get_record` |
| Problem evidence count | The evidence items of the valid version that start before `now` | `get_record` |
| Metric envelope | The end is the last visible point; the length is the count of visible points | `query_metrics` |
| Channel list | The channels with a visible message | `read_chat`, inventory |
| Channel members | Never shown, as a list or as a count | none |
| Time coverage per source | The first and last visible record time | inventory |

**K5. Stats tables and sampled estimates.** `group_by=minute` prints one count per bucket, zeros included. Past 60 buckets the tool widens the bucket (1m, 5m, 15m, 1h) and says so. Other groupings print one count per group over the window, the top 20 and an "other" row. An estimate is the sum of `keep_1_in` over the matched records; the label gives the rate, or the lowest and highest when they differ, with the unit.

**K6. Changes that have not started.** A change or build is listed when its span, from its start to its end or to `now`, overlaps the window. A visible change whose start is after `now` is listed and marked `planned` when `until` is omitted; with an explicit `until`, the footer counts them. A planned time is a scheduled time, so the leak test exempts it by field (section 6.6).

**K7. Rows and referenced, the undecided cases.** A stats or template result carries one row: its handle is the `q:` handle and its text is the whole table. A template line's example record is `referenced`, since a masked line is not that record's content. `check_status_page` with no provider lists pages as `referenced`. Two knowledge hits on one page are two rows with the same handle. When several records state the same edge, the lowest handle is the row and the rest are `referenced`.

**K8. Error sentences.** One fixed sentence per case, each with a test: bad arguments, naming the field; a well-formed handle that is not visible, whether it is unknown, not yet visible or absent ("not in this bundle", one sentence, so the three cannot be told apart); a malformed handle; an empty result, naming the filter to drop or the window to widen; a clamped window; an internal error, with no traceback and no path. `get_record` on a `q:`, a windowed `metric:` or an `ic:note/` handle says the handle is minted per call or per run and names what to re-run. `status` is `error` for all but empty and clamped.

**K9. Topology.** Match methods, tried in this order and each printed with the candidate: exact; casefold; domain stripped at the first dot; pod suffix stripped; an IP address to the CI that lists it; an external id the CI records. No literal domain or name appears in code. Edges: CMDB relationship rows; the relationship lists on monitored entities; and a `calls` edge from `service` to `peer.service` for any visible monitoring record tagged with both. Each matched CI prints what PLAN section 4 lists and no more: its owner with the active flag, its support group, its operational status and its external ids. Who is on call stays with `get_oncall`. The alias test reads its expected table at test time; the build session does not open that file.

### 6.6 The tool-output half of the leak test

It extends `tests/test_leak.py`, which already holds the scanner, the checkpoints, the tokens and the exemptions. It runs at both checkpoints, with tags off and with tags on. The build session uses what that module loads and does not open the file behind it. No token, checkpoint id or expected value is written under `smalls/`.

- **What it scans.** `Envelope.text`, every `Row.text`, `native_query`, `window`, `meta` and the inventory, with the store half's scanner.
- **What fails.** A time later than `now`. A time equal to `now` is allowed only as the clamped end of the window. A future token for the checkpoint, anywhere. A printed `valid_to`, or a `t_end` at or after `now`.
- **What is exempt.** The store half's scheduled times, by field, and each is printed with the word `planned`: a change request's planned start and end, an on-call shift's end. Actual work, close and update times are never exempt.
- **The call set.** The widest window with no filter for the five windowed tools; every `table` for `itsm_query`; `get_record` for every handle in the store, where a handle not yet visible must return byte for byte what an unknown handle returns; every service and policy for `get_oncall`; every CI at 2 hops for `get_topology`; each provider and none for `check_status_page`; the retrieval-check queries for `search_knowledge`; every served query for `query_metrics`; the inventory.
- **Derived-state assertions.** A monitor's state and a paging service's status are derived, never the served values. A versioned record is the version valid at `now`: problems, builds, paging incidents, the live incident, status-page snapshots. A closed problem or a finished build does not exist before its `valid_from`. Thread counts, repliers and latest reply come from visible replies. Channel members never appear. A metric envelope's end and length come from visible points.
- **Coverage.** Every visible record's handle appears in `rows` or `referenced` of some call in the set, so no record is ingested and then unreachable. A record kind that no call lists must be on a short allow-list with its reason. To make that hold, an incident's audit rows are among its children, and a synthetic test is `referenced` from its monitor's row. And for every visible record, the parts of `get_record` joined hold every non-empty field it may show, in full (G7).
- **Defects and caps.** The duplicate delivery is one row with two resolving handles; the truncated log line is flagged; the alert that fires and resolves keeps both transitions; hidden empty fields are counted. Every tool stays under its caps, and every truncated result carries its footer.

One limit carries over from the store: a versioned record is invisible for the instant `now` equals one of its save times. No checkpoint sits on one. A tool that is given an arbitrary `now` behaves the same way, and we do not work around it.

### 6.7 The surface, restated for the prompt author

What each tool reads and returns, without any name from the dataset. Parameter names and types are in the schema file.

| Tool | Reads | Returns | Cap |
|---|---|---|---|
| `list_alerts` | Monitor events and definitions, APM problems, log-search alert deliveries, network-monitor alerts, paging incidents | One row per alert stream: handle, source, native id, title, the raw entity string as the source wrote it, first seen, last change, state at now, how many times it changed state, and any linked paging incident and saved search. A flapping stream is one row with a count | 40 rows |
| `get_record` | Any handle | The whole native record as it stood at now; empty fields hidden and counted; children listed as handles. A record larger than the cap comes in parts, and nothing is dropped. This is the call that returns in full whatever another tool showed shortened | 3k tokens per part |
| `search_logs` | Raw logs | Rows: the total, then handle, UTC time, host, sourcetype and the raw line, with stack traces folded. Or a count table by minute, host, level or template. A count from a sampled source carries its sampling rate and an estimate. The equivalent native search is echoed | 4k tokens |
| `query_metrics` | Metric series | Up to 4 series rolled to at most 45 points, gaps kept; minimum, maximum, first breach of a monitor threshold and runs of missing data; a handle for the series over the window | 2k tokens |
| `list_changes` | Pipeline builds and change requests | Rows side by side, never joined or ranked: handle, kind, id, CI or job, summary, version, change ticket, start, end, state at now, requester | 30 rows |
| `itsm_query` | Incidents, journal entries, users, groups | Field values as they stood at now; the native query is echoed | 3k tokens |
| `search_knowledge` | Runbooks and knowledge pages; close notes and work notes of incidents | Ranked passages: handle, title, last updated, a snippet. A closed incident returns its whole close note with CI, category and close code. Ranking is plain keyword ranking and is never tuned | 3k tokens |
| `get_topology` | CMDB items and relationships, monitored entities, service-call tags | Matching items with each alias candidate and how it matched, owner and whether that owner is active, support group, operational status, external ids; edges up to 2 hops with type, direction, status and the source each came from. Sources can disagree, and the disagreement is left visible | 40 edges |
| `get_oncall` | Paging services, escalation policies, on-call shifts | Escalation levels, who is on call at now, the escalation timeout | 0.5k tokens |
| `read_chat` | Chat channels | Messages: handle, UTC time, display name, text with mentions resolved, reply count at now; the replies when a thread is given | 60 messages |
| `check_status_page` | External providers' status pages | Indicator, incidents and updates as of now; with no provider, the known pages | 0.5k tokens |

Common to all: `now` is injected and never a parameter; every row starts with a handle, and only a handle whose content was shown can be cited; times are UTC with the native value beside it where it differs; a result that was cut says what was cut and how to narrow, and a row that was shortened says so; an error comes back as text to act on.

## 7. The agent

### 7.0 Framework

The framework is settled in agent-design sections 1.1 and 2, and this plan builds on it unchanged: a custom LangGraph `StateGraph` with explicit nodes, and models reached through LangChain so the provider is a config value. Not the Claude Agent SDK, which works with one provider and whose state is a transcript; not a hand-written loop, which lacks a resume in another process, crash recovery, typed reducers and one multiplexed stream.

Pins, carried from the spike, with minor-version caps because the event stream and delta channels are still moving:

| Package | Pin | Resolved by the spike's lock | Used for |
|---|---|---|---|
| `langgraph` | `>=1.2,<1.3` | 1.2.15 | The graph, reducers, interrupts, streaming |
| `langgraph-checkpoint-sqlite` | `>=3.1,<3.2` | 3.1.2 | `SqliteSaver`, one checkpoint database per run |
| `langchain` | `>=1.4,<1.5` | 1.4.5 | `init_chat_model` |
| `langchain-core` | `>=1.6,<1.7` | 1.6.10 | Messages, `content_blocks`, the scripted chat model |
| `langchain-anthropic` | `>=1.7,<1.8` | 1.7.6 | The default provider |
| `langchain-openai` | `>=1.7,<1.8`, optional group | not installed | The cross-provider verifier profile |
| `rich` | `>=13` | 15.0.0 | The terminal |

Where each piece is used. A reviewer checked every name below against the installed versions.

| Framework feature | Where | Note |
|---|---|---|
| `StateGraph(InvestigationState, context_schema=RunContext)` with plain-function nodes | `graph.py`: orient, sweep, agent, gate, ask, tools, verify, finalize | Axis 1, option (b) |
| Reducers: `add_messages`, a field-wise board merge, `operator.add` | `state.py` | `messages` is append-only; a custom reducer must be an inspectable callable |
| Runtime context (`Runtime[RunContext]`) for `now`, run id, policy flags and budgets | every node | LangGraph does not checkpoint it, so `run.json` keeps it and a resume passes it again |
| `interrupt()` and `Command(resume={interrupt_id: {call_id: decision}})` | the `ask` node and the runner | The policy lives in `gate`, which never re-runs; `ask` only interrupts. Resume by id: a bare value fails when several interrupts are pending |
| `SqliteSaver` | `runner.py` | Default durability for attended runs; `durability="exit"` for matrix runs, which still persists state after an in-process error |
| `stream(..., stream_mode=["messages", "updates", "custom"], version="v2")` and `get_stream_writer()` | `runner.py` only | With these three modes an interrupt arrives as an `updates` part whose data holds `__interrupt__`; only `values` parts carry an `interrupts` field. `invoke(version="v2")` returns a `GraphOutput`, not a dict |
| `RetryPolicy(max_attempts=2)` with our own predicate, and an explicit `timeout=` | model nodes | The default predicate retries the provider's 400 and 401 errors. Ours retries timeouts, connection errors, 429 and 5xx only. The attempt number comes from `runtime.execution_info.node_attempt` |
| `GraphRecursionError` | caught in `runner.py` | The runner renders the partial report from state |
| `init_chat_model` behind our adapter | `models.py` | Per-provider kwargs, not `configurable_fields`, which leaks one provider's defaults into another's request |
| `bind_tools` with our per-provider schema adapter | `models.py` | A native tool dict keeps `strict: true`; an OpenAI-format dict loses it |
| `content_blocks` | `models.py` | Maps each provider's reasoning to one block type |
| `ToolMessage(status="error")` | the `tools` node | Rejections and tool errors, in the same user message as the batch's other results |
| `usage_metadata` | the `agent` node, into state | Usage survives a pause or a crash |

Deliberately not used: `create_agent` with `HumanInTheLoopMiddleware` and `ToolStrategy`; `with_structured_output` or a forced tool call for the report; `SummarizationMiddleware` and provider-side context editing; `astream_events`; the beta v3 event stream; `DeltaChannel`, which is the production path for checkpoint size but still beta. LangSmith stays off unless its environment variable is set.

### 7.1 Layout

```
smalls/agent/
  __main__.py              run, resume, replay, schema, blind-room
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
  solve/                   the scripted solve path
  runs/<run_id>/           run records
tests/agent/  tests/tools/  offline
```

The agent takes the trigger as text and `now` as a time. Resolving a checkpoint to a trigger file is the job of `eval/` and the Makefile, so nothing under `smalls/` names `triggers/`. The store's path guard covers the loader only, so this is allowed.

One process holds one store and one clock. A resume in a new process rebuilds the store, which is deterministic, and sets the clock from `run.json`. The matrix runner starts one process per run, so two checkpoints can never share a clock.

`python -m smalls.agent` is the agent's entry point and stays; `smalls run`, `smalls resume` and `smalls replay` are added to the `smalls` command as aliases in row C3.

### 7.2 The run record and config

The format is the scorer's start condition, so it is fixed here. `eval/runs/<run_id>/` holds:

| File | Rows or keys |
|---|---|
| `run.json` | Run id, arm, checkpoint, `now`, bundle, profile, commit, prompt hash, tool-schema hash, tags flag, sweep windows, package versions, the names of the key variables that were set |
| `events.jsonl` | `{seq, run_id, t_wall, type, data}`; replayable with no model |
| `calls.jsonl` | `{call_id, round, by, tool, kind, args, rationale, status, shown, referenced, native_query, window, chars, latency_ms, repeat_of, used_tag_input, gate}`, where `by` is `agent`, `harness` or `carry_forward` |
| `evidence.jsonl` | `{call_id, handle, t_known, sha256, text}`, written at call time |
| `notes.jsonl` | `{handle, text, submitted_at, by, call_id}` |
| `board.json` | The board as it stood at the end, with its per-change history |
| `report.json`, `verdict.json` | The report with code-written fields; the verdict and the revision diff |
| `usage.json`, `summary.json` | Per-call tokens, cost and latency; totals, gate waits, retries, process metrics |
| `checkpoints.sqlite` | The checkpointer; git-ignored |

The runs index is `eval/runs/index.sqlite`, git-ignored. A note is given at a gate card or with `resume --note`.

Config keys in `config.toml`: `call_budget`, `revision_calls`, `extra_turns`, `stale_rounds`, `stuck_empty`, `context_fraction`, `headless`, a `[sweep]` table, a `[verifier]` table (`enabled`, `claims_profile`, `contrarian_profile`, `digest_tokens`), `[profiles.*]` and `[pricing.*]`. Starting values, re-set after the first measured run: 30 gated calls; 5 revision calls; 6 extra turns; stale after 3 rounds; stuck after 3 empty results; restart at 85 percent of the model's input limit; sweep windows of 2 hours for alerts and chat and 24 hours for changes; the verifier on.

### 7.3 Where the design needed a concrete form

Agent-design fixes the method. These are the judgment calls made to turn it into code. Each is ours to overrule.

| # | Design text | Concrete form |
|---|---|---|
| J1 | "The board is typed state"; the model writes judgements | `update_board` carries hypotheses, timeline rows and open questions. `source` is set when a hypothesis is created and cannot change. |
| J2 | "Ready" when the leading hypothesis has non-chat support for each link and one discriminating check, and every rival has `against` | The model lists in `discriminating[]` the handles it claims would exist only if the hypothesis were true. Code signals ready when: one hypothesis is `leading`; it has a non-chat handle in `support` and in `discriminating`; every other hypothesis that is not rejected and has role `trigger` or `contributing` has non-chat support; and every other hypothesis with role `cause` has an `against` handle. A handle that entered the run as `carry_forward` does not count toward ready. |
| J3 | "Classed with chat" | `handle_class()` in `smalls/contracts/handles.py` is the one definition, shared with the scorer. `slack:` and `ic:note/` handles are chat. See open point P4. |
| J4 | "A repeated identical call returns the earlier result" | Identity is the tool name plus the validated arguments, with defaults filled and relative times resolved, without `rationale`. Only an earlier call that ran and returned ok makes a repeat. The repeat gets a one-line pointer to the earlier round and costs nothing. A restart clears the repeat index, because the earlier text is no longer in context. |
| J5 | "The budget is checked per call" | A read or an action counts when it runs. Internal calls, sweep calls, repeats, policy refusals, rejections, deferrals and argument errors do not. A revision adds 5 calls and 7 turns. Model turns are capped at the budget plus `extra_turns`. |
| J6 | "`inconclusive` is a legitimate status" | A submitted report with no `leading` hypothesis is inconclusive. It is valid when it has at least one open question. No schema field is added. |
| J7 | The alert verdict list (G3) | The validator wants one verdict for every alert stream the sweep showed. A report that misses some gets its retry with the list; after that, code records the rest as `unassessed`. |
| J8 | "Its cited rows are re-served this run" | `run --carry-from <run_id>` seeds the board from that run's `board.json`. Record handles are re-fetched with `get_record` and entered in the ledger as `carry_forward`. Aggregate and windowed metric handles change with `now`, so their calls are re-run with the earlier resolved window and the seeded board cites the new handle. A handle that no longer resolves is dropped from the seed and listed. Earlier notes are not carried. |
| J9 | "Must cite at least one handle whose `t_known` falls after the earlier `as_of`" | The check passes when the leading hypothesis cites a row whose `t_known` is after the earlier `as_of` and which a ledger entry other than `carry_forward` showed. An aggregate handle never satisfies it. A failure is a validator finding with its retry; after that the report carries `continuity: failed` in `validation`. |
| J10 | "Reads the incident channel" | The channel is an input, `--channel`. Without it, the sweep reads the newest messages across all channels up to the cap. The matrix and `make run` pass no channel. |
| J11 | "A tag is a query facet, never evidence" | Tag-dependent aggregates mint no citable handle (section 6.3). |
| J12 | The sweep "goes through the same gate policy as any read" | The sweep's calls are written to state as pending calls with proposer `harness`, and `gate` reads pending calls from state, not only from the last model message. In `--interactive` the sweep is one card. Its results enter as one user message after the first turn, written by code: one block per call, headed by the tool and its arguments, then the rows as printed. No assistant turn is fabricated. |
| J13 | The verifier's packets and verdict | Claim ids are `h:<id>`, `t:<n>`, `a:<rank>` and `d:<n>`. "In the leading hypothesis" means `h:<leading>` and every `a:` row. The verifier answers through a `submit_verdict` tool carrying the axis 5 schema; a verdict that does not parse gets one retry, then the report stands unverified. An accepted `submit_report` is answered by `verify`, not by `tools`, so the verdict can come back as that call's result. The digest is the rows shown but not cited, newest first, 200 characters each, inside `digest_tokens`. Claim support defaults to the cheaper tier of the investigator's provider; the contrarian pass defaults to the investigator's profile. |
| J14 | Stale, stuck, caps and the validator's retry | One retry per submission cycle covers every validator finding. After it, a bad handle is dropped and recorded, and a claim left with no handle is kept and flagged. A confidence above a cap is clamped, and the result says so; a second `leading` is refused. The status line flags a stale board from the second round; at `stale_rounds`, or with the budget spent, the gate refuses reads and actions with "submit now". Stuck is `stuck_empty` consecutive ok reads with no rows: a hint from the inventory and a `stuck` event, nothing refused. The status line has one form: `[board: ready | not ready: <missing>; stale: <ids>; budget <used>/<limit>, <n> turns left]`. |
| J15 | "Headless: approve and record, for evals only" | A gate decision's `by` is `{who: policy | auto | human, via: cli | headless}`. Approve-and-record is the default only for a run started by the matrix runner or with `--headless approve`; any other unattended run defers. The hard guard refuses headless approval when any action tool has `mock=False`. |
| J16 | "Restart with a fresh conversation whose first turn is rendered from the board" | A restart is a new thread, `<run_id>.<n>`, seeded with every channel except `messages`, at most once per run. The limit is the profile's `max_input_tokens`, else the model's own. Until row E2 builds the restart, the guard ends the run `incomplete`. |

### 7.4 The fixture registry

Offline agent tests run over a synthetic incident that has nothing to do with the dataset. `tests/agent/fixture_registry.py` exposes `registry()` and `inventory()`: fixture versions of `search_logs` (rows and stats), `get_record` (with one child in `referenced`), `list_alerts`, `list_changes` and `read_chat`, each on the real argument model and returning the real envelope, with rows that appear between two values of `now`. That covers the sweep, the alert verdicts, a referenced-only citation, an aggregate and carry-forward. `build_graph` takes a registry and an inventory function.

The spike's 19 tests are ported with their intent, not their letter: the headless-default test asserts approve-and-record for an eval run and gains twins for `defer` and `reject`, and the proposal test reads the `by` shape of J15.

### 7.5 Provider facts to confirm in the live shakedown

Each is asserted in agent-design from the provider reference. Row C4 confirms it against a live call and records the number or the outcome in agent-design section 6.

1. Reasoning summaries stream as text when asked for; between-tool notes arrive as reasoning, so visible text deltas are nearly empty.
2. Forced tool choice is rejected, so `submit_report` and `submit_verdict` are always the model's choice under `auto`.
3. Strict tool schemas pass through our adapter; numeric bounds are stripped and enforced by our own validation.
4. The provider accepts strict schemas across the whole surface: about fourteen tools, many with optional inputs.
5. The cache-read ratio is above zero from the second call, with effort fixed for the run.
6. Reasoning blocks replay unchanged and the history is never edited; the append-only test holds on a live transcript.
7. Parallel results return in one user message, and a rejection arrives as an error result beside them.
8. Usage fields price correctly, including the split cache-write counters.
9. An explicit timeout is set, and long outputs stream.
10. No sampling parameter is sent.
11. One live call succeeds from inside the container, with keys arriving only through the `env_file`.

A refusal stop reason cannot be provoked on demand, so its handling (server-side fallback if the client library passes it through, else the fallback profile once) is confirmed by a scripted test, not a live call.

Two options to try, neither required: streaming the report tool's input as it is generated, so the draft report appears early; and sending harness text (the status line, notes) through the provider's operator channel where one exists, so it is not mixed with tool output.

## 8. Build order and acceptance

One sequence. Each row is committed when its acceptance passes. A row that needs a go-ahead or another session is reported as waiting, and the build continues with the next row that does not depend on it.

Live runs spend money. None starts without our go-ahead, and the matrix is sized after the first measured run.

### Phase A: foundations

| # | Build | Accepted when |
|---|---|---|
| A0 | Rebase onto `step6a-ingestion`. Confirm section 6.1 and work through section 9. | `make data`, `make lint`, `make test` and `make docker-test` pass on the rebased branch; any difference from 6.1 is written into this record |
| A1 | Contracts: the envelope, the registry types, the argument models for all eleven tools with `rationale`, `handle_class()`, the report model with G3 and its generated JSON Schema | A full example report and a partial one both validate; the schema file matches the model; `python -m smalls.agent schema` prints every tool and report schema, with tags on and off, and `--size` prints the prefix size; the scorer session can start |
| A2 | Port the LangGraph spike to `smalls/agent/` (agent-design row 1), on the pins in 7.0, with the registry injected and the fixture registry of 7.4. With it: the stable v2 stream parts, caching on by default, the measured context-window guard (axis 2), the headless rule of J15, the validator checking shown handles, `ic:note` handles, the run-record files of 7.2 | The ported tests pass, the bundle-string and model-name tests among them; a scripted run over the fixture registry writes a complete run record; a replay renders it with no key. In the container (section 10.5): the rebuilt image passes `make docker-test`, a scripted run writes its record on the mount, and a run paused in one container resumes in another |

### Phase B: five tools

| # | Build | Accepted when |
|---|---|---|
| B1 | Tool plumbing: `call`, the renderer and caps, windows, the error module, `derive.py`, the golden test harness | Each error case of K8 has a test; a result over its cap fails `call` |
| B2 | `search_logs`, rows and stats, with its tag input | Its row in 6.4. The agent runs over the real registry and writes a record: the done condition of agent-design row 1 |
| B3 | `list_alerts`, then `list_changes`, with their tag inputs | Their rows in 6.4 |
| B4 | `read_chat`, `get_record`, `smalls tools call` | Their rows in 6.4 |
| B5 | The tool-output half of the leak test (section 6.6); `make tools-smoke` | `make leak-test` covers the store and every tool built so far, and grows with each later tool |

### Phase C: the first run

| # | Build | Accepted when |
|---|---|---|
| C1 | The method in code (agent-design rows 3 and 4): `board.py`, the sweep of J12, `update_board`, `submit_report` with the full schema, `proposed_actions` from the ledger, `do_not`, `stakeholder_update`, the provider schema adapter, `propose_action`, and the verdict schema and packet layout of J13 | A unit test per cap and per status-line flag; no unknown or referenced-only citation survives the validator; a report with a missing alert verdict gets its retry |
| C2 | The blind prompt pass (G4), in the blind room. **Needs a fresh session.** | The placeholder gate clears; the slot check and the bundle-string lint pass; the commit holds only the five files |
| C3 | The sweep on real tools; a minimal inventory (log sourcetypes and hosts, chat channels) until row D2; `make run`, `make resume`, `make demo`. **Needs a go-ahead.** | **First end-to-end live run at the second checkpoint** writes a complete run record, and that record is committed as the one `make demo` replays |
| C4 | Live shakedown (agent-design row 2): the list in 7.5; budgets set from measurements. **Needs a go-ahead.** | Real numbers in agent-design section 6; the matrix cost is known before any matrix runs |

### Phase D: hardening and the other six tools

| # | Build | Accepted when |
|---|---|---|
| D1 | Failure handling (agent-design row 5 and section 8.4): one row of axis 8 at a time; records written in `finally`; `error` on every failure and `run_finished` on every exit that is not a pause; tolerant reader; isolated sinks; coalesced reasoning deltas with one full `thinking` event per round; a `cache_ratio_low` event; server-minted run ids; the runs index; `deferred`; `ic_note` | One scripted test per row of axis 8; an injected provider timeout leaves a full record and a resumable checkpoint; no file in a run directory contains a key value |
| D2 | `search_knowledge` and the source inventory | Their rows in 6.4; the full inventory replaces the minimal one in the first turn |
| D3 | `query_metrics`, `check_status_page` | Their rows in 6.4 |
| D4 | `search_logs group_by=template` | The top-N cut and the "other" row; counts labelled when sampled |
| D5 | A scripted solve path of about 15 CLI calls, kept in `eval/solve/`, never near a prompt | The path reaches the cause. **Gate B** is then run by a fresh session with only the CLI. If the cause cannot be found, we fix the bundle, not the agent. |
| D6 | Run records and the matrix (6c): `eval/matrix.py` with `durability="exit"`, arm C, and arm A in `eval/baseline.py`, which calls `read_chat` and `list_alerts` through `call` and then makes one model call with only `submit_report` bound; process metrics in `summary.json`; `make regress`, `make matrix` | One command runs arm C at the second checkpoint three times; that is the regression set from here on |
| D7 | `itsm_query`, `get_oncall`, `get_topology` with alias and edge derivation | Their rows in 6.4; the leak test covers all eleven tools |
| D8 | The retrieval check: about 10 queries phrased as an incident commander would ask them | Recall@1 and MRR reported as a sanity check |
| D9 | Tags acceptance across every tool, and the tag flag as a matrix dimension | Section 6.3 holds for every tool with tags on. With no tag input used, a tags-on result with its labels and tag footers removed equals the tags-off result. The leak test passes with tags on |
| D10 | The terminal (the CLI part of the UI row): sticky header from the board, one card per round, numbered citations, `show <handle>`, heartbeat | A run paused in one process, or in one container, resumes in another, and what the two print equals what a replay prints; every tool string is rendered as text |

### Phase E: the verifier, carry-forward and the scored loop

| # | Build | Accepted when |
|---|---|---|
| E1 | Verifier (agent-design rows 7a and 7b): code checks, packets from `evidence.jsonl`, the uncited-evidence digest, claim support, the contrarian pass, the revision rules, the changed-claims re-check, the flag, both model profiles | Verdicts recorded; a scripted unsupported claim triggers exactly one revision; a contrarian check already in the ledger triggers none |
| E2 | Carry-forward and restart (agent-design row 8): the board renderer, J8, J9 and J16 | A second-checkpoint run seeds from the first; a carried leading hypothesis with no new handle is flagged; a scripted run with a low input limit restarts once and keeps its ledger |
| E3 | The keep-or-kill rule for the verifier, on the regression set, for both profiles. **Waits for the scorer and its claim-support judge.** | The rule in agent-design axis 5 is evaluated and the result recorded |
| E4 | Carry-forward from the first checkpoint to the second, live | The continuity check passes |
| E5 | The tags ablation: the regression set with the flag off and on | The numbers PLAN section 6.1 names are reported, whichever way they point |
| E6 | Docs: HANDOFF, agent-design section 6, the edits of section 12 | A fresh session can start the next step from HANDOFF alone |

**If time runs short**, cut in this order: the contrarian pass and its revision (E1, second half); `ic_note`; the `untagged` filter value; row D10 beyond what the port already renders. Never cut the leak test, the validator, the run record or replay.

### Done means

- From a clean clone with a key, one command investigates the bundle at the second checkpoint and writes a schema-valid report with zero fabricated, unseen or future citations. With no key, one command replays a committed run.
- With only Docker installed, the tests, the replay and a run all work through `docker compose run`, and a run started in a container can be resumed or replayed from the host.
- The leading hypothesis cites a non-chat handle for each link; each rejected hypothesis states what contradicts it; the report lists what not to do; every alert stream the sweep showed has a verdict. Once the scorer exists, its owner check passes.
- The agent stops on its own inside its budget, and a run that degrades says so: `incomplete`, dropped citations, policy rejections and `stuck` events are all counted.
- A run paused at a gate resumes in a new process. A rejected action reaches the model with its reason and appears in `proposed_actions` as written by code.
- The leak test passes for the store and all eleven tools at both checkpoints, with tags off and with tags on.
- With tags off, no tool output mentions a tag. With tags on, no result hides an untagged row without saying how many.
- Every prompt and description was first written in the blind room, and git shows it. No string under `smalls/agent/` or `smalls/tools/` contains a bundle string; no model name appears outside `config.toml`.
- The regression set runs with one command, and every run writes a complete record, failed runs included.
- The carry-forward run from the first checkpoint cites at least one handle known only in the gap.

## 9. Still to check at the rebase

Section 6.1 was read from the ingestion branch, not from a rebased tree. Row A0 checks these and writes any difference here.

1. The ingestion branch has moved since `df733de`: re-read its HANDOFF "Next" section and DATA entries 98 onward.
2. The tagging pass has an experiment mode for candidate facets. If a facet is added or dropped, the registry follows the profiles, and the argument models and their blind-written descriptions change with it.
3. The `attrs` keys each tool needs per source: version and change ticket for builds, thresholds for monitors, the link fields behind K2. A tool reads `native` through JSON1 where `attrs` lacks one.
4. How a metric envelope `ref` row is named, and where a served query's filter, group tags and aggregator are read from (K3).
5. How `prompt_texts()` is extended to our five files and to string literals under `smalls/agent/` and `smalls/tools/`.
6. The CLI's bundle-path helper, which `smalls tools call` and the run commands reuse.
7. Whether the existing base-table test also needs `clock` added to its list, since no tool may set the clock.
8. The tags default (G6). It is one default in `store.tags_enabled()`, the ingestion tests that assert the old one, and the lines that state it in CLAUDE.md, PLAN (decision 12, section 4 and section 6.1), `ingestion-decisions.md` (O2 and T6) and HANDOFF. If the ingestion session is still running, it makes the change on its branch and this track picks it up by rebasing; otherwise row A0 makes it.

## 10. Running in Docker (G2)

The agent runs inside the one compose service that ingestion built. No service is added in this step: LangGraph is a library in the app process, its checkpointer is a SQLite file, and nothing listens on a port until the UI step. `uv` on the host stays the primary path, and the container runs the same commands for someone who has only Docker.

### 10.1 What we checked

On 2026-10-13 we built a throwaway image on the project's base (the same `python:3.12-slim-bookworm`, the same `uv`, the virtualenv outside the source tree, the same non-root user) with the spike's locked dependencies, ran it and removed it.

- The LangGraph and LangChain packages at the pins in 7.0 install from wheels on arm64. No compiler and no extra system package is needed.
- The spike's 19 offline tests pass inside the container as the non-root user.
- A scripted run paused at a gate in one container and was resumed, with a rejection, in a second one. The run record and the checkpoint database sat on a bind-mounted folder between the two, and the recorded gate wait matched the real gap.
- The container's SQLite is 3.40.1, with FTS5 and JSON1. That is older than a host Python's is likely to be.
- The image has no git, so the run record's commit came out empty.
- The resume hint printed a host command.

The last three are fixed by the rules in 10.4.

### 10.2 What changes, and in which row

We build on the ingestion branch's Docker setup as it stands and change as little of it as we can, because that session is still running and every line we leave alone is a conflict that cannot happen. Row A0's rebase brings `Dockerfile`, `compose.yaml`, `.dockerignore` and the Makefile in byte for byte; nothing is copied by hand. The structure stays theirs: one service called `smalls`, an image that holds dependencies only, the repository bind-mounted at `/app`, keys through `env_file` at run time, and `docker compose run --rm smalls <the host command>` as the way to run anything.

| File | Change | Row |
|---|---|---|
| `Dockerfile` | None for the agent. Its packages are core dependencies, and the existing sync line installs those. The environment is fixed at build time (`UV_NO_SYNC=1`), so the image is rebuilt when a dependency changes; `make docker-test` already rebuilds. One group joins the sync line later, when the cross-provider verifier profile needs its client | none, then E1 |
| `.dockerignore` | None. It is an allowlist of what the Dockerfile copies, and the agent lives under `smalls/`, which is already on it | none |
| `compose.yaml` | One added block on the existing service, and nothing else touched: an `environment:` list that passes `SMALLS_TAGS`, `SMALLS_PROFILE`, `SMALLS_MODEL`, `SMALLS_COMMIT` and `SMALLS_LIVE` through by name and sets `SMALLS_RUNNER=compose` | A2 |
| `Makefile` | The existing stubs `run`, `demo` and `tools-smoke` are filled in, and `resume`, `regress` and `matrix` are added, all in the style of the targets already there (`$(UV) run --python 3.12 ...`, the `BUNDLE` variable, the optional `--env-file .env`). `docker-test` is left as it is | B5, C3, D6 |
| `pyproject.toml`, `uv.lock` | `langgraph`, `langgraph-checkpoint-sqlite`, `langchain`, `langchain-core`, `langchain-anthropic` and `rich` join `dependencies` at the pins in 7.0. `langchain-openai` goes in an optional group in row E1 | A2 |
| `.gitignore` | Two added lines: checkpoint databases and the runs index | A2 |

Two things this worktree needs differ from the main checkout, and neither is a tracked change. The ingestion session builds `smalls:dev` from the main checkout while this worktree builds an image with more packages in it, and with one tag whichever built last would win. And the worktree has no `.env`. Compose loads a `compose.override.yaml` beside `compose.yaml` on its own, so the worktree keeps one, untracked and listed in `.git/info/exclude`, that sets `image: smalls:agent` and points `env_file` at the main checkout's `.env`. It is deleted when the branches meet.

### 10.3 The same commands, both ways

| What | On the host | In the container |
|---|---|---|
| Offline tests | `make test` | `docker compose run --rm smalls make test` |
| One tool call | `smalls tools call <name> --now <time> ...` | `docker compose run --rm smalls smalls tools call <name> --now <time> ...` |
| Replay a committed run, no key | `make demo` | `docker compose run --rm smalls make demo` |
| A live run, gates asking | `make run` | `docker compose run --rm smalls make run` |
| Resume a paused run | `smalls resume <run_id>` | `docker compose run --rm smalls smalls resume <run_id>` |
| The regression set, unattended | `make regress` | `docker compose run --rm -T smalls make regress` |

Run records land in `eval/runs/` on the bind mount either way, so a run started in a container can be replayed, resumed or scored from the host, and the other way round.

### 10.4 Rules the container makes necessary

1. **A test never goes live because a key happens to be set.** Compose passes the whole `.env` into every container, tests included; on the host a key reaches a command only through `--env-file`. So nothing decides to call a model by looking for a key. Under pytest the profile is the scripted one, live checks run only when `SMALLS_LIVE=1`, and a test asserts both.
2. **The commit comes from the host.** The image has no git, and a worktree's `.git` points at a host path. `run.json` takes the commit from `SMALLS_COMMIT`, which the Makefile sets, then from git, then records "unknown".
3. **A hint prints the command for the way the run was started.** With `SMALLS_RUNNER=compose` the resume line is the compose form.
4. **Attended or not is decided by the terminal.** `docker compose run` attaches one, so gates ask; `-T` or `--headless` makes the run unattended, and then J15 applies: only an eval run approves and records, any other unattended run defers.
5. **An interrupted run still leaves its record.** Ctrl-C reaches the process, records are written in `finally` (row D1), and `--rm` removes the container, not the files on the mount.
6. **State stays on the bind mount.** The checkpoint database worked there across two containers. `SMALLS_STATE_DIR` moves it if it ever proves slow.
7. **Nothing depends on a newer SQLite than the container's.** The suite runs in both places, and a result that differs between them is a bug in the tool, not in the test.
8. **Keys arrive only at run time,** through the `env_file`. `make docker-test` already checks that the image holds none, a run record stores the names of the variables that were set and never their values, and row D1 scans each run directory for key values.
9. **A live run needs outbound HTTPS to the model provider** and nothing else. No port is published.

One limit we inherit and do not change here: the container user has a fixed uid, so on a Linux host with a different uid the bind mount may not be writable. The run instructions say so.

### 10.5 Acceptance in the container, by row

| Row | Checked in the container |
|---|---|
| A0 | `make docker-test` passes on the rebased branch |
| A2 | The image is rebuilt with the agent's packages and `make docker-test` passes with the agent's tests; a scripted run writes a complete record on the mount; a run paused in one container resumes in another |
| B5 | The leak test passes there, at both checkpoints |
| C3 | The run that `make demo` replays renders the same in the container as on the host |
| C4 | One live call succeeds from inside the container, with keys arriving only through the `env_file` |
| D1 | An interrupted container run leaves a full record |
| D6 | The regression set runs unattended with `-T` |
| D10 | The terminal renders in an attached container; pause and resume across containers print what a replay prints |

## 11. Open points

| # | Point | Default the build uses |
|---|---|---|
| P2 | The action surface: one `propose_action` with a closed `kind` list, or several typed tools. | One tool. Its `kind` list and wording are written in the blind room from standard incident-response vocabulary, so the list is not shaped by this incident. |
| P3 | Accept `untagged` as a value of each tag input. PLAN lists the inputs but not this value. | Accept it. It adds a way to reach rows and removes none. |
| P4 | Are human-written journal entries and paging notes classed with chat? PLAN names only `ic:note` beside chat. | No: only `slack:` and `ic:note/`. The scorer session owns the final answer, through `handle_class()`. |
| P5 | Which run records are committed. | The first complete run, which `make demo` replays, and the runs the write-up cites, without their checkpoint databases. |
| P6 | A mechanical guard for the held-out rule in this worktree: a sparse checkout that leaves the three paths off disk, or deny rules in the session settings. | None yet; the rule is kept by discipline. The blind room already guards the prompt author. |
| P8 | How held-out bundles get tags (ruling O4, deferred). With tags on by default (G6), a held-out run at the default would find no cached tags and every record would read as untagged. | Not ours to settle on this track, and nothing waits on it until a held-out run. Two ways out: the held-out authoring session runs the tagging pass on its bundle, or held-out runs set the flag off and the write-up says so. |

P1 (the tags default) and P7 (the 2k clip) were ruled on 2026-10-13: G6 and G7.

## 12. Edits to other documents, applied in row E6

- **PLAN 3.1**: `subject`, `alerts[]` and `source`; which fields code writes; the per-checkpoint shape of `history`; "inconclusive" defined as no leading hypothesis.
- **PLAN 3.2**: `handle_class()` as the one definition of the chat class.
- **PLAN 0, 4 and 6.1, CLAUDE.md, `ingestion-decisions.md`, HANDOFF**: tags are on by default (G6), with O2 marked as replaced and O4 as needing an answer before a held-out run.
- **PLAN 4**: the envelope with `rows[]` and `t_known`; the argument conventions of K1; tag-dependent aggregates are not citable; the `untagged` bucket and value, if P3 is ruled in; `get_record` pages and nothing is clipped (G7).
- **PLAN 6**: the status line; this record as the order for 6a-tools through step 7.
- **PLAN 13**: a pointer to this record.
- **`docs/decisions/step6-decisions.md`**: a pointer at ruling A1 to the envelope here.
- **HANDOFF**: the two review points are ruled (G3), with the scorer and data halves still open; status and next step.
- **CLAUDE.md**: this record in the doc map; the blind-room rule; the new make targets.
- **agent-design section 6**: measured numbers after row C4.
- **DATA**: the re-measured OR-join result for `search_knowledge`.
