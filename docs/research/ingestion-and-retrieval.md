# Ingestion and retrieval: how Smalls reads a bundle

Research and decision doc, 2026-10-13. It settles the earlier ingestion options draft (since removed) against `docs/PLAN.md` and `docs/schemas.md`. It was written without reading any held-out or answer-bearing path. No bundle had been generated yet, so volumes are PLAN targets, not measurements.

Claim tags: **[V]** verified (I read the primary source or ran it here), **[S]** search results only, **[U]** unverified.

## Summary

- **Ingest with code, in one deterministic pass, at process start.** One small adapter per source under `smalls/tools/ingest/` loads the bundle into in-memory SQLite. No model, no disk cache; at about 4,100 lines and 210 records a rebuild should take about a second [U].
- **Use stdlib SQLite and drop rank-bm25.** FTS5 gives BM25 for prose, `LIKE` or `instr` gives substring search for logs (no trigram, ruled D3), and JSON1 reads native fields. All work on this container's Python 3.12.3 (SQLite 3.45.1) and on the uv-managed 3.12.13 (SQLite 3.53.1) a reviewer gets from a clean clone [V].
- **Visibility is one view.** Every tool selects from `visible_record`, which joins a one-row `clock(now)` table. The predicate is `coalesce(t_known, t_event) < now`, inside the record's version window.
- **Code handles syntax; the agent handles meaning.** Code does clocks, folding, dedupe, flap collapse, derived state and alias proposals with a recorded method. The agent judges relevance, causality, CMDB trust and human-written times.
- **Retrieval, as ruled by the step 6 panel (section 3a).** Agentic retrieval through structured tools, BM25 over prose, a source inventory in context, and two-hop topology on `get_topology`. No vectors and no similar-incident scoring in v1. GQL or Cypher, GraphQL, GraphRAG and growing the corpus were researched and rejected.
- **Evals early and in depth (section 3c).** Eight layers, from tool parity to generalisation, running from the first end-to-end agent run. They do not replace held-out: only held-out evidences generalisation. Nothing is cut to make room for now.
- **Vector tooling fixed for spikes (section 3d).** Voyage `voyage-4-lite` over stdlib HTTP, a committed embedding cache, exact cosine in numpy over visible chunks, and RRF. Off by default and behind a config flag, with BM25 as the fallback.
- **Built for scale (section 3b).** The agent never depends on the data fitting in context. The headline eval compares context strategies (no tools, stuff everything, the agent with tools) at bundle size and at a scale render with sampling off.
- **Effort.** The build order and hours in `docs/decisions/step6-decisions.md` section 4 supersede section 7 here. The approaches eval adds about 4.5 h in step 10.

### Decisions (generator-affecting first)

| # | Decision | Recommendation |
|---|---|---|
| 1 | Visibility time | Add an optional `t_known` to the manifest (null means equal to `t_utc`), and require every `t_known` to be derivable from served bytes. |
| 2 | Source time zones | The generator emits a served `bundles/<b>/integration.yaml` with each source's zone, its record-break rule, and the year for syslog. |
| 3 | Store | In-memory SQLite. FTS5 with porter stemming for prose; `LIKE` or `instr` for logs. No trigram (ruled D3). Remove rank-bm25 from PLAN section 0. |
| 4 | Embeddings | None in v1, with no recall gate (ruled D4). Hybrid search is the production path. |
| 7 | Retrieval additions | A source inventory in context, and two-hop topology on `get_topology` (ruled R1 and R2). Similar-incident scoring is rejected for v1 (R3). |
| 8 | Scale and the approaches eval | The agent never depends on the data fitting. Compare context strategies at bundle size and at a scale render (section 3b). Proposed; not yet ruled. |
| 5 | Enrichment | Annotate, never overwrite. CMDB and alert values appear side by side, each with its source. |
| 6 | Log templates | A regex masker for `group_by=template`. No Drain3. |

**Update after Gate A.** `docs/DATA.md` entries 24 and 25 adopted decisions 1 and 2 while this doc was being written. They make `t_known` a *required* column set by `known_rules:` in the scenario YAML, not an optional column set by per-row `known_at`. Either works, and the Gate A form is simpler for the tools, which can drop the `coalesce`. Two points from decision 1 still apply on top of it: every `t_known` must be derivable from served bytes, and Splunk alert trigger times need a served field. Decision 2's `break_only_before` per source is an addition to entry 25.

**Rulings applied.** `docs/decisions/step6-decisions.md` ruled on every open point here on 2026-10-13. Where the two differ, that record wins. The summary, the decisions table and section 3a have been updated to match. Section 7's effort table is the original estimate; the step 6 build order supersedes it.

## 1. What I verified

| Item | Finding | Tag |
|---|---|---|
| SQLite here | Python 3.12.3, SQLite 3.45.1, `sqlite3` module 2.6.0, compiled with `ENABLE_FTS5` and `ENABLE_MATH_FUNCTIONS`. JSON1, weighted `bm25()`, `snippet()` and trigram all work. An external-content FTS rebuild over 4,100 rows took 10.6 ms. | V (ran) |
| SQLite from a clean clone | `uv python install 3.12` gives CPython 3.12.13 with SQLite 3.53.1. FTS5, trigram and JSON1 all work. I checked Linux only. | V; macOS U |
| Tokenizers | unicode61 matches `"HikariPool-1"`, `"timeout-ms=8000"` and `"waiting=37"` as phrases. `rollback` does not match "Rolling back"; with porter, `roll` and `exhaust` do match. Trigram finds substrings such as `"ms=80"`. Trigram `MATCH` under 3 characters returns nothing; `LIKE` still works. | V (ran) |
| DuckDB FTS staleness | The docs say: "The FTS index will not update automatically when the input table changes" ([full_text_search.md](https://github.com/duckdb/duckdb-web/blob/70642ff/docs/preview/core_extensions/full_text_search.md)). FTS is downloaded on first use. Here the download failed because `extensions.duckdb.org` is blocked, and a reviewer behind a proxy would hit the same failure. | V |
| DuckDB vss | Index persistence is experimental, and the docs "recommend that you do not use this feature in production" ([vss.md](https://github.com/duckdb/duckdb-web/blob/70642ff/docs/preview/core_extensions/vss.md)). | V |
| Chroma local BM25 | There is none. The Search API, which does the sparse and BM25 ranking, "is available in Chroma Cloud only" ([overview](https://github.com/chroma-core/chroma/blob/5f30478/docs/mintlify/cloud/search-api/overview.mdx)). Local Chroma has only the `$contains` and `$regex` filters ([full-text-search](https://github.com/chroma-core/chroma/blob/5f30478/docs/mintlify/docs/querying-collections/full-text-search.mdx)). | V |
| Splunk zone precedence | From `props.conf.spec` 10.4, lines 584 to 593: a zone in the raw text comes first, then `TZ`, then the forwarder's zone, then the zone of the host running splunkd. The same file defines `BREAK_ONLY_BEFORE` and `TRUNCATE` (default 10,000 bytes) ([spec](https://github.com/splunk/vscode-extension-splunk/blob/de2edfb/spec_files/10.4/props.conf.spec)). | V |
| BigPanda alert object | It has `primary_property` and `secondary_property`, status ok, critical, warning, unknown or acknowledged, and an epoch-second `timestamp` that defaults to the time of receipt ([parameters](https://docs.bigpanda.io/en/parameters), [alert object](https://docs.bigpanda.io/reference/alert-object)). | S |
| BigPanda CMDB enrichment | When an alert tag shares a key with a CMDB field, the CMDB value wins. An "Exported Override Columns" setting controls that precedence ([best practices](https://docs.bigpanda.io/en/servicenow-cmdb-enrichment-best-practices)). | S |
| Embedding prices | Voyage `voyage-3.5-lite` costs $0.02 per million tokens, with the first 200M free ([pricing](https://docs.voyageai.com/pricing)). OpenAI `text-embedding-3-small` costs $0.02 per million ([model page](https://developers.openai.com/api/docs/models/text-embedding-3-small)). | S |
| Packages (PyPI) | [rank-bm25](https://pypi.org/project/rank-bm25/) 0.2.2: last release 2022-02-16, needs numpy. [sqlite-vec](https://pypi.org/project/sqlite-vec/) 0.1.9 is pre-1.0. [fastembed](https://pypi.org/project/fastembed/) 0.9.0 pulls in huggingface-hub and numpy. [drain3](https://pypi.org/project/drain3/) 0.9.11: last release 2022-07-17. | V |
| Contextual retrieval | Contextual embeddings cut top-20 retrieval failures by 35% ([cookbook](https://github.com/anthropics/claude-cookbooks/blob/9c1e493/capabilities/contextual-embeddings/guide.ipynb)). Adding contextual BM25 gives 49%, and adding reranking gives 67%. The same post says a knowledge base under 200k tokens can simply go in the prompt ([post](https://www.anthropic.com/news/contextual-retrieval)). | 35% V; rest S |
| Splunk scheduler `sid` | Scheduled-search sids embed the scheduled time as `_at_<epoch>`. | U |

The egress policy here blocks sqlite.org, duckdb.org, BigPanda, Voyage, OpenAI and Splunk docs, so every [V] comes from a vendor's GitHub repository, PyPI, or running code here.

## 2. Decisions

### Decision 1 (generator): visibility on `t_known`

**Problem.** PLAN 3.4 makes a record visible when `t_utc < now`. Some records, though, are published after the event they describe:

- A Splunk alert's `result._time` is when the matching line was written; the webhook fires later, when the scheduled search runs.
- A change request can be raised after its deploy, so `sys_created_on` is later than `start_date`.
- A metric point stamped 14:51 is complete only at 14:52.

If `t_utc` means event time, these leak into earlier checkpoints; if publication time, the scored timeline is wrong. One column cannot do both.

**Recommendation.** Add `t_known: str | None = None` to `ManifestRow`, and make visibility `coalesce(t_known, t_utc) < now`. The tool layer never sees the manifest, so the rule that keeps this honest is that **every `t_known` must be derivable from served bytes**. Status-page snapshots need no `t_known`: their `page.updated_at` already gives `valid_from`. Metric buckets need no manifest change either, because code applies the rule `t + interval <= now`.

**The generator must emit:**

- (a) `t_known` on rows whose scenario event has a publication lag, from a new optional `known_at` beside `at` in the scenario YAML;
- (b) Splunk alert trigger times in a served field: a scheduler-style `sid` ending `_at_<epoch>_<n>` [U], else the next `cron_schedule` tick after `_time`;
- (c) `sys_created_on` set to when a change was raised, never to its planned start.

**Cost.** About 0.5 h. A new lint checks `t_known >= t_utc` and that it re-derives from the served record; the leak test and 60-second rule switch to `coalesce`.
**PLAN changes.** Sections 3.2 and 3.4, and invariants 5, 8 and 10.

### Decision 2 (generator): a served `integration.yaml`

**Problem.** PLAN decision 9 gives machine clocks to code. But several sources carry no usable zone:

- Logback lines have no zone.
- ServiceNow display values are in the integration user's zone, with no marker.
- SolarWinds times are server-local.
- F5 syslog headers have no year and no zone.

Zones exist only in `scenario/facts.yaml`, which is never served. Without a served config the tool layer would hard-code zones, which breaks when a bundle changes one.

**Recommendation.** The generator writes `bundles/<b>/integration.yaml`, rendered from the zones in `facts.yaml`. It holds what an admin of each integration would know, in the vocabulary of `props.conf`:

```yaml
syslog_year: 2026
splunk:
  payments_app: {tz: <zone>, time_format: "%Y-%m-%d %H:%M:%S,%f", break_only_before: '^\d{4}-\d{2}-\d{2} \d{2}:'}
  checkout_api: {tz: null}   # zone in the event
  postgres:     {tz: null}
  nginx:        {tz: null}
  f5:           {tz: <zone>}
servicenow: {display_value: "true", user_tz: <zone>}
solarwinds: {server_tz: <zone>}
```

`break_only_before` makes folding a served rule, as in Splunk, so multi-line handles match the generator's (DATA.md decision 3). The orphan `at ...` frame folds into the truncated record before it; the generator must hash it the same way.

It converts only machine clocks; human-written times stay with the agent.

**Cost.** About 0.25 h, plus pointing invariant 8 at this file. It is a root file, not a vendor folder, so it does not count against the source ceiling.
**PLAN changes.** Sections 2, 3.5 and 8.

### Decision 3: SQLite FTS5, not rank-bm25 in memory

PLAN section 0 lists rank-bm25 as a dependency; the draft recommends SQLite. **Choose SQLite, held in memory.**

| | rank-bm25 over Python lists | SQLite FTS5 + JSON1 |
|---|---|---|
| Clean-clone install | rank-bm25 and numpy; last release 2022 | stdlib; verified on uv's Python |
| Visibility | a filter repeated in every tool | one view |
| Stats and time buckets | hand-written loops | `GROUP BY t_event / 60000` |
| Log substring search | write it yourself | `LIKE '%term%'` or `instr` in SQL |
| Inspectable | no | `smalls ingest --dump x.db` |

In memory removes the draft's cache invalidation, so `make run` needs only uv. DuckDB is stronger for metrics, but its FTS downloads at first use (failed here) and goes stale on writes: reviewer friction that buys nothing at 4,000 lines.
**Cost.** About 1 h for the schema, the view and the loader.
**PLAN changes.** Section 0 (dependencies) and section 4 (`search_knowledge`).

### Decision 4: no embeddings in v1

**Recommendation.** `search_knowledge` uses FTS5 with porter stemming and a title weight of 3, over heading-sized chunks prefixed with the page title and its "Last updated" line. The 25-query eval is committed (section 3a): it makes the vectors decision a measured one.
**Reason.** About 18 documents; the one gap is paraphrase, which stemming and the agent's rephrasing mostly cover.
**Cost.** 1.5 h for the eval. Hybrid search, if the eval ever justifies it, adds about 2 h plus a key or a model download.

### Decision 5: annotate, never overwrite

**Recommendation.** Confirm the draft. Show `support_group`, `owned_by` and `business_service`, each with its source; mark an inactive owner `active=false`; and list CMDB and Dynatrace edges side by side.
**Reason.** BigPanda's precedence is configurable [S], so this is a choice, not a departure. Overwriting hides the disagreements a commander must notice.
**Cost.** None.

### Decision 6: regex templates, not Drain3

**Recommendation.** Add `template` as a `group_by` value in `search_logs`. A masker of about 20 lines replaces numbers, hex, UUIDs, IPs and quoted values with `<*>`.
**Reason.** "Which kinds of line jumped at 14:31?" is the question stats mode exists to answer. Drain3 has had no release since 2022.
**Cost.** 0.25 h.
**PLAN changes.** Section 4.

## 3. Where retrieval fits

Sizes: section 3a has the numbers measured on the slice bundle; logs are about 275k tokens, and the prose is under 10k.

| Source | Retrieval | Vectors would be | Why |
|---|---|---|---|
| Raw logs | time, host and sourcetype filters; substring match; per-minute stats; templates | theatre | The signal is exact tokens and counts over time. |
| Alerts, problems, pages | time and state filters, collapsed per stream | theatre | Correlation is by time and entity, as in BigPanda. |
| Metrics | a series query; code computes the summary | theatre | Numbers. |
| Changes and builds | a time window plus keyword, sorted by start | theatre | Ranking them is the agent's job. |
| Tickets, journal, users | `itsm_query` filters | theatre | Fields. |
| Chat (~75 messages) | time, thread, keyword | theatre | A whole channel is a few thousand tokens. |
| Closed incidents (~10) | per-category structured score plus BM25 (section 3a) | maybe, after the eval | The similar-incidents mechanic, where paraphrase is real. |
| Runbooks and KB (~8) | an index in context, plus BM25 over heading chunks | low value | They fit in a prompt. The tool exists for handles and visibility. |
| CMDB and entities (~15 CIs, ~20 edges) | alias lookup, then up to two hops by recursive CTE, including mention edges | a graph database would be theatre | An edge table answers every question we have. |

**Theatre at this size:** embedding logs, metrics or chat; a vector database for 18 documents; a graph database or GraphRAG for 35 nodes and edges; a model parsing logs at ingest; pre-written model summaries stored as "memory"; a "retrieval agent" wrapping one SQL query; a reranker.

PLAN section 2 expects plain retrieval to rank a misleading prior incident first, so never tune BM25 against specific documents: the agent must read past the top hit.

**For the write-up**, which must cover RAG, vector search, and knowledge graphs or structured context:

> Retrieval in Smalls is agentic and structured, and built as if the data will not fit. The agent chooses what to fetch through tools that filter by time (clamped to the replay clock), entity and source over one normalised evidence table. The tools aggregate before anything reaches the model, and every result carries a citable handle. The knowledge graph is the topology: edges from the CMDB, Dynatrace and monitoring tags, each kept with its source so their disagreements stay visible, traversed up to two hops. Prose is searched with BM25. We measured the corpus at under 20k tokens, so vectors would add a dependency without adding recall. Hybrid search with BigPanda-style per-category similarity is the production path. The approaches table shows what this costs and saves: the same agent with no tools, with everything stuffed into context, and with tools, at bundle size and at twenty times the log volume. The structured context that matters most is the agent's own investigation state: hypotheses linked to supporting and contradicting handles.

Update the approaches sentence with the measured result before submitting.

**Production shape.** A real estate writes about 75,000 gateway log lines in this incident's window alone, and BigPanda serves many tenants. Four things would change:

- Logs stay in the customer's Splunk and Datadog; the echoed native query becomes the real API call.
- Alerts, changes, topology and history move to a multi-tenant store (Postgres with row-level security, or columnar for event volume); `t_known` becomes the pipeline's receive time.
- Template mining such as Drain runs first, so 75k lines reach the model as a few hundred templates with counts.
- Prose grows to thousands of documents per tenant, where hybrid BM25 plus vectors, fused with Reciprocal Rank Fusion and scoped by tenant and permission, starts to pay.

The agent's tool interface stays the same, which is the reason to build it this way now.

## 3a. Retrieval options: what would actually add value

This section was added after the first version, in answer to: should Smalls do RAG over a graph (GQL), use vectors or embeddings, and which of these would bring value rather than merely be possible? I judged each option by one test: what does the incident commander get that they would not get otherwise?

**Measured sizes.** These are from the slice bundle generated after the first version, served files only, at characters divided by 4 [V, counted]:

| Source | Approx. tokens |
|---|---|
| Splunk logs (3,355 lines) | 275k |
| Datadog | 29k |
| Slack | 9.5k |
| ServiceNow | 7.5k |
| Jenkins | 7k |
| Dynatrace | 4.5k |
| Confluence | 0.9k |

So the prose that retrieval would serve is under 10k tokens in the slice, and probably 15k to 20k in the full bundle. Logs are the only source too big to put in a prompt.

**Settled.** The step 6 panel ruled on these options on 2026-10-13 (`docs/decisions/step6-decisions.md`, rulings R1 to R4 and D4). This table records the outcome. The arguments for the rejected options are not repeated.

| Option | Status | What ships |
|---|---|---|
| Document index in context | **Adopted as a source inventory (R1)** | The inventory is built at runtime from visible records. It lists knowledge documents, metric series, saved searches, channels, log sourcetypes and hosts, and time coverage. It is injected once per checkpoint in the first user turn, so the system prompt and tools stay a stable cached prefix. At scale it falls back to counts plus the top N per kind (section 3b). |
| Two-hop topology | **Adopted on `get_topology` only (R2), in step 7** | A recursive CTE follows edges from the CMDB, Dynatrace and Datadog `peer.service`, each labelled with its source. `near=` on `list_changes` and `list_alerts` is rejected for v1. |
| Per-category similar-incident scoring | **Rejected for v1 (R3)** | `search_knowledge` returns BM25 hits over close notes and work notes, with the whole note, CI, category and close code. The write-up names BigPanda's categories (entity, problem, impact, topology) as the production path. |
| Retrieval eval | **Small, off the critical path (R4)** | About 10 queries, reported as recall@1 and MRR, described as a sanity check. The "+10 points recall@5" gate is dropped (D4): at 5 to 18 documents any retriever scores near 100%. |
| Hybrid BM25 plus embeddings | **Not in v1 (D4); the production path** | A `vec0` KNN query and an FTS5 plus vector fusion in one SQL statement ran here [V], so no second store would be needed. The local model download failed here with a 403 [V]. Voyage costs $0.02 per million tokens [S] but needs a key. |
| Grow the knowledge corpus to about 200 incidents so RAG is needed | **Rejected** after three independent critiques | It builds the problem to fit the answer. At 150k tokens the corpus would still be under the 200k "put it in the prompt" bar. Model-written history reads uniform. With journal rows it is about 800 records against a 250 ceiling. Every incident would need hand screening for leaks. The estimate is 16 to 20 h, not 9. And a 40-query test cannot detect a 10-point gain: the smallest detectable difference is about 15 to 20 points. |

**Not worth it here:**

| Option | Why not | Evidence |
|---|---|---|
| The agent writes graph queries (GQL or Cypher) | GQL became an ISO standard (ISO/IEC 39075) in April 2024, and SQL/PGQ (ISO/IEC 9075-16:2023) brings the same pattern matching to SQL [S]. But embedded Python options are thin. Kùzu runs here, but the project is archived; its last release, 0.11.3, came on 2025-10-10 ([README](https://github.com/kuzudb/kuzu/blob/89f0263/README.md)) [V]. DuckPGQ calls itself "a research project and a work in progress", and its extension downloads at runtime ([README](https://github.com/cwida/duckpgq-extension/blob/33d6051/README.md)) [V]. Neo4j and Apache AGE need a server. Model-written Cypher is still error-prone by Neo4j's own Text2Cypher analysis [S]. At 35 nodes and edges, a two-hop pattern is just a tool parameter. Option 2 delivers the capability without any of this. | V and S |
| GraphQL as the agent's query interface | It would mean one typed tool, with introspection and field selection. But it needs a schema and a server, the agent has to learn that schema, and caps, footers and visibility all move into resolvers. Existing text-to-GraphQL MCP servers ship a separate validation tool, a hint that generated queries fail [S]. It also runs against PLAN decision 7 and Anthropic's advice to build a few task-shaped tools rather than wrap an API [S]. It would be reasonable later as the UI's API. | S |
| Microsoft GraphRAG (a graph and community summaries extracted by a model) | The repository is now in maintenance mode and warns that indexing "can be an expensive operation" ([README](https://github.com/microsoft/graphrag/blob/5faaaf4/README.md)) [V]. Our entities are already structured, so extraction would re-derive the CMDB less accurately. Model-written summaries are not citable evidence. | V |
| Embedding logs, chat or metrics; semantic log clustering | At 3,355 lines, template masking (decision 6) does the clustering deterministically. | — |
| A reranker | With k ≤ 5 over about 18 documents, the agent is the reranker. | — |
| Retrieval over Smalls' own past reports | Every run investigates the same incident. A 14:52 report retrieved at 14:40 would leak the future and contaminate the evals. Recurring-incident memory is a production feature. | — |

## 3b. Built for scale, and the eval that proves it

This section adds four working assumptions:
- the agent has to scale;
- the logs are huge and will not fit in context;
- the agent goes to production, where it gets hammered;
- evals that compare approaches show more than a single score does.

This section adopts the first three as a design rule and adds the eval.

**Design rule: the agent never depends on the data fitting.** The slice already does not fit comfortably: 3,355 log lines come to about 275k tokens [V, counted]. The served logs are a documented sample. PLAN section 2 puts the unsampled estate at about 75,000 gateway lines for the window. At the slice's average line length that is several million tokens [U, extrapolated]. So:

- No tool returns a whole file or an unbounded set. Every result has a cap and a footer (section 5).
- Aggregation happens inside the tool (stats, templates, metric summaries). The agent's token use grows with its questions, not with the data volume.
- Counts from sampled sources say "sampled 1:N, estimated X" (D6).
- At scale, the source inventory shrinks to counts plus the top N per kind, and search does the rest.

**Under load in production.**

| Concern | v1 | Production |
|---|---|---|
| Data volume | In-memory SQLite over a sampled bundle | Tools push queries down to the Splunk, Datadog and ServiceNow APIs. The echoed native query becomes the real call. |
| Cost per investigation | Caps per tool; tokens logged per run | A budget per investigation (tool calls, tokens, wall time), enforced by the harness and shown to the agent |
| Repeated questions | Nothing cached | Tool results cached by normalised query and `as_of`. Handles are deterministic, so cached results stay citable. |
| Slow or failing sources | The error contract (A2) | Timeouts, backoff, partial results that name the missing source, and a circuit breaker per vendor |
| Concurrency and tenancy | One investigation, one bundle | A stateless tool layer, investigations run as independent jobs, every query scoped by tenant and permission |
| Knowledge base | BM25 over less than 20k tokens | Hybrid BM25 plus vectors fused with RRF, plus BigPanda-style per-category similarity |
| Observability | Handles logged per call (A1) | A trace per tool call, cost per investigation, and an eval regression run on every prompt change |

**The approaches eval.** One table in the write-up shows the same agent under different context strategies, at bundle size and at estate scale. It extends the no-tools baseline in PLAN step 10, so part of it is already planned.

| Arm | Context strategy | Data |
|---|---|---|
| A | No tools: the trigger, the Slack transcript and the alert titles in one call (PLAN's baseline) | slice |
| B | Everything served, in one long-context call, no tools | slice, about 350k tokens |
| C | The agent with tools (what ships) | slice |
| D | The agent with tools | scale render: the same scenario with log sampling off |
| E | Everything served, no tools | scale render. It does not fit, so report its size, or truncate it to the window and score that. |

For each arm, run 3 to 5 times at 14:52, and at 14:40 if time allows. Record:
- the scorer's outcome: root cause, traps avoided, citations valid;
- input tokens, cached and uncached;
- cost, wall time and tool calls.

Report the results as descriptive, with the spread between runs, not as significance claims.

The comparisons answer three questions:
- **C against B:** do tools cost accuracy when the data could fit?
- **C against D:** does the agent's cost stay flat when the logs grow about twentyfold?
- **E:** where does stuffing break?

Together they turn the scalability claim into a measurement. They also answer the question "why not just put it all in context?" with data.

**What it needs** (estimates are [U]):

- **A scale render.** The generator renders the same scenario with log sampling at 1:1, into a git-ignored folder. It sits outside the 5,000-line ceiling and is never committed or submitted. The served `sampling:` block (D2) already records the rates. This is data-session work, about 1.5 h raw.
- **Ingest at about 75k lines.** Expect seconds. The caps already bound what reaches the agent.
- **Arm wiring in the harness.** 0.5 h.
- **Runs and the table.** 1 h, plus API cost. Arm B is about 350k input tokens per run and needs a long-context model.
- **Total.** About 3 h raw, 4.5 h with the 1.5x correction.

**Where it goes:** step 10, beside the no-tools baseline and the contrarian control. If time is short, keep A, C and D. D against C is the scalability evidence, and B is cheap if a long-context model is available. The scale render uses the development scenario at a different sampling rate. It is not a variant and not a substitute for held-out evidence.

## 3c. Eval plan: early and in depth

**Principle.** Evals start with the first end-to-end agent run, not at step 10. From then on, every change to the prompt, the tools or the data is judged by them. The tables in the write-up are generated from `eval/runs/`, not written by hand.

**Layers.** Each layer answers a different question. A result in one layer never stands in for another.

| # | Layer | Question | Measure | From |
|---|---|---|---|---|
| 1 | Tool layer, no model | Is ingestion correct, and can the future leak? | Handle parity, the leak test, caps (section 6) | step 6 |
| 2 | Retrieval check | Does `search_knowledge` find the obvious document? | About 10 queries, recall@1 and MRR (R4) | step 7 |
| 3 | Outcome | Did the agent get it right? | The scorer against the answer key: root cause, traps avoided, recommended actions | step 7 |
| 4 | Grounding | Is every claim backed by what it cites? | (a) The validator: every cited handle exists and was visible. (b) Claim support: a judge checks that each claim's handles support it. About 20 claims are also checked by hand, and the agreement is reported. | (a) step 6, (b) step 8 |
| 5 | Process | Did it investigate efficiently? | Tool calls, repeated calls, tokens per cited handle, time to the first correct hypothesis, budget hits | step 6 |
| 6 | Robustness | Is it steerable by a bad trigger, or brittle to names? | Trigger-framing perturbations, alias shuffle, contrarian control (PLAN step 10), at both checkpoints | step 9 |
| 7 | Approaches and scale | Does tool-based retrieval beat stuffing, and does cost stay flat as data grows? | Arms A to E (section 3b) | A and C from step 7; D once the scale render exists; B and E in step 10 |
| 8 | Generalisation | Does it work on an incident it was not built on? | Held-out h1 and h2 (PLAN section 12) | after `agent-v1` |

**Mechanics.**
- Each run writes `eval/runs/<run_id>/`: the config (arm, checkpoint, agent commit), the trace with handles per call (A1), the report, the scores, and tokens, cost and time.
- One command runs a matrix of arms, checkpoints and repetitions.
- A small regression set runs on every prompt or tool change: arm C, 14:52, 3 runs.
- The full matrix runs before the `v1-submittable` tag, at 5 runs per cell.
- Results are reported as a mean with spread, never as a single run.
- Size the matrix after the first run has measured tokens per run, so the API spend is known before committing to it.

**Why this does not replace held-out.** The two answer different questions.
- **Layers 1 to 7 run on the development incident.** The agent is built and tuned against it, so a strong score there can mean "it learned this incident". The deeper and earlier these evals run, the more tuning they drive, and the larger that risk becomes.
- **Layer 8 is the only evidence of generalisation.** Held-out variants are authored by a separate session that never sees agent code, prompts or transcripts. They are scored once, after the `agent-v1` freeze, and never used for tuning.
- **The scale render does not substitute.** It is the same incident at a different sampling rate. It tests cost and robustness to volume, not generalisation.
- **So in-depth development evals make held-out more necessary, not less.** If h1 cannot be run, the write-up says "no held-out evidence" (PLAN section 6).

**Schedule and cost** (raw hours; multiply by 1.5):

| When | Work | Raw h |
|---|---|---|
| Step 6, first end-to-end run | Run-record format, the matrix runner, arms A and C, the validator, process metrics | 1.5 |
| In parallel now (data session) | Scale render with log sampling off, git-ignored | 1.5 |
| Step 7 | The scorer (already planned) scores every run from here on | 0 extra |
| Step 8 | Claim-support judge with a hand-checked sample; arm D | 1.5 |
| Step 10 | Full matrix including robustness and arms B and E; tables generated into the write-up | 1.5 |
| **Total new** | | **6, about 9 corrected** |

PLAN step 10 already budgets 2.5 h for the baseline, controls, perturbations and runs. That overlaps the last row, so the net addition is about 6.5 corrected hours.

## 3d. Vector retrieval tooling (spikes)

Vectors are not in v1 (D4). This section fixes the tooling now, so a spike can start without reopening research. The decisions are written as defaults, not options.

**What gets embedded.** Knowledge chunks only:
- runbook and KB sections, each prefixed with the page title and its "Last updated" line;
- one chunk per closed incident: short description, CI, category, close code and close notes;
- one chunk per work note.

Logs, metrics and chat are never embedded (section 3).

**Defaults**

| Concern | Decision | Why | Evidence |
|---|---|---|---|
| Embedding model | Voyage `voyage-4-lite` at 512 dimensions, with `input_type` set to `document` for chunks and `query` for queries | The cheapest model in the family, about $0.02 per million tokens, with Matryoshka dimensions from 256 to 2048. 512 keeps the cache small. | Price and dimensions [S]. `input_type` and `output_dimension` are parameters of the client's `embed()` [V] |
| Client | Call `POST https://api.voyageai.com/v1/embeddings` with stdlib `urllib`, not the SDK | The `voyageai` 0.5.0 SDK pulls in aiohttp, pillow, tokenizers, langchain-text-splitters and numpy [V, PyPI]. One HTTPS call needs none of them. | The base URL is in the SDK's `util.py`, and the resource name is `embeddings` ([voyageai-python@9aca465](https://github.com/voyage-ai/voyageai-python/tree/9aca465)) [V] |
| Key and reproducibility | Commit a document-embedding cache, keyed by `sha256(model, dimension, input_type, text)`, outside `bundles/`. Commit the eval queries' embeddings the same way. Live agent queries need `VOYAGE_API_KEY`. Without it, `search_knowledge` falls back to BM25 and its footer says so. | Tests and `make demo` then never touch the network. A reviewer without a key still gets a working system and a reproducible eval. | — |
| Vector search | Exact cosine in numpy over the visible chunks: the SQL `visible` view supplies the ids and vectors, numpy scores them. No ANN index. | Visibility filtering is exact and needs no over-fetch. 10,000 vectors of 1,024 dimensions score in 0.7 ms warm [V]. numpy goes in an optional `retrieval` dependency group. | Ran here |
| In-SQL alternative | sqlite-vec 0.1.9, using `vec_distance_cosine()` over the visible rows. It took 11.7 ms over 5,000 of 10,000 rows at 1,024 dimensions [V]. | Use it if fusion in one SQL statement is preferred. It is pre-1.0 and needs SQLite extension loading, which is unverified on macOS Pythons. | Ran here |
| Fusion | Reciprocal Rank Fusion with k = 60, in Python | Standard, needs no tuning, and is LanceDB's default | RRF paper, Cormack et al. 2009 [U, not re-read] |
| Agent interface | `search_knowledge` keeps its signature. The mode is config (`SMALLS_RETRIEVAL=bm25` or `hybrid`), never a model choice. Each hit says which retriever found it and its rank in each list. | Eval arms switch the mode, and the agent can see why a hit came back | — |
| Not chosen | Local keyless models. `voyage-4-nano` needs torch and sentence-transformers [V, README]. fastembed with `bge-small-en-v1.5` needs onnxruntime (63 MB) and a Hugging Face download, which failed here with a 403 [V]. Also not chosen: Chroma (no local BM25 [V]), LanceDB, Kùzu (archived [V]), pgvector or OpenSearch (they need a server). | Reviewer friction for no gain at this size | — |

**Spikes, time-boxed, with exit criteria** (raw hours):

| # | Spike | Exit criterion | h |
|---|---|---|---|
| S1 | Embedding client and committed cache | A second run makes zero API calls. Tests pass with the network off. A missing key gives BM25 with a footer note. | 1 |
| S2 | Hybrid `search_knowledge` behind the flag | The leak test passes in hybrid mode. Hits label their retriever. Every BM25 hit in the top k is still reachable in hybrid mode at 2k. | 1 |
| S3 | Evaluate | The retrieval check (layer 2) runs in both modes, and arm C runs in both modes in the approaches eval (layer 7). The decision and its numbers go in the write-up. | 0.5 |
| S4 | Stretch: semantic search over record summaries (change short descriptions, commit messages, alert titles, incident short descriptions) for the `text=` filter of `list_changes` and `list_alerts` | A query worded differently from the summary ("retry" against "resilience") finds the record that keyword search misses. Production rationale: at thousands of changes, the agent cannot list the whole window. | 1 |
| S5 | Stretch: a local keyless model | Install size and first-run download measured. Adopt only if it works offline after first run and adds under about 200 MB. | 1 |

S1 to S3 take about 2.5 raw hours, 3.75 corrected. The stretches take 2 raw hours.

## 4. The boundary between code and agent

This answers "tradeoffs between simple code and agentic behavior" and "useful agent behavior, not agent theater" (`requirements.md`, *Technical Expectations*, *What We Care About*). Rule: if a deterministic, testable function exists and a wrong answer is not a judgement call, code does it.

| Code normalises (testable, no model) | The agent judges |
|---|---|
| Clocks to UTC via `integration.yaml`, keeping `t_native` and `tz_rule`; DST folds flagged with `zoneinfo` (checked: 01:30 on 2026-11-01 in New York is -4 h at fold 0, -5 h at fold 1) | Times humans wrote, shown with the author's Slack `tz` |
| Folding by `break_only_before`; truncation flagged; traces shown to 3 frames | Whether a trace shows a cause or a symptom |
| Duplicate webhook deliveries collapsed by `sid` (`deliveries: 2`; both handles resolve) | Whether an alert matters |
| Flapping streams collapsed to one row with a transition count | Whether a flap is noise |
| State derived at `now` (Jenkins, monitors, PagerDuty, Dynatrace); ServiceNow rewound through `sys_audit` | What a state change implies |
| Alias proposals (casefold, strip domain and pod hash, IP to CI), each with its method and source; non-matches stay visibly unresolved | Whether to trust a match; what a missing CI means |
| Facts surfaced: an inactive owner, edges from each source, the ticket's group next to the escalation policy | Who owns the problem; which edge is stale |
| Nothing semantic: no causal links, no ranking of changes, no parsing of psql tables | Everything that requires weighing evidence |

## 5. Ingestion mapped onto the tools

**Store.** A `record` table holds one row per record version, with these columns: `handle, version, source, dataset, kind, native_id, host, entity_raw, entity_id, stream_key, t_event, t_end, t_known, valid_from, valid_to, t_native, tz_rule, sev, sev_text, status, title, body, parent, attrs (JSON), native, path, locator, flags (JSON)`. Side tables: `handle_alias`, `alias`, `edge`, `point (series, t, value, interval_s)` and `chunk`. One FTS5 index, `chunk_fts` (porter unicode61), over knowledge chunks. Logs are matched with `instr`; at about 3,000 to 75,000 rows a scan takes milliseconds.

```sql
CREATE TEMP VIEW visible_record AS
SELECT r.* FROM record r, clock c
WHERE coalesce(r.t_known, r.t_event) < c.now
  AND (r.valid_from IS NULL OR r.valid_from <= c.now)
  AND (r.valid_to   IS NULL OR c.now < r.valid_to);
```

The harness sets `clock.now` per call; no tool reads `record` directly (tested); `until` is clamped to `now`.

**Caps.** Tokens are characters over 4; drop rows from the end until the result fits, then add a footer with the total and a way to narrow, e.g. `[showing 50 of 1,240; narrow with host=, terms=, a shorter window, or group_by=]`. The native query echo comes last.

| Tool | Query | Visibility | Cap |
|---|---|---|---|
| `list_alerts` | `GROUP BY stream_key` over alert, problem and page rows: first seen, last `valid_from`, state at now, transition count | view; state is the version valid at now | 40 rows; footer suggests `source=`, `state=active` |
| `get_record` | handle via `handle_alias`, then the version valid at now; children by `parent` | a not-yet-visible handle gets the same answer as an unknown one | 3k tokens; `""` fields hidden and counted; text clipped at 2k |
| `search_logs` rows | each term becomes `instr(lower(body), lower(?)) > 0`, joined with AND; host and dataset filters; ordered by time; with a total count | view | 50 rows of 300 chars |
| `search_logs` stats | `GROUP BY t_event/60000` (or 5 min) plus host, dataset, severity or template | view | 60 cells |
| `query_metrics` | `point` by series and scope, rolled up in SQL to at most 45 buckets; Python computes min, max, null runs, and first breach against a monitor threshold | `t + interval_s <= now` | 4 series, 2k |
| `list_changes` | builds and changes overlapping the window, ordered by start; `VERSION` and `CHANGE_TICKET` from `attrs` | view; derived or rewound state | 30 rows |
| `itsm_query` | `table` maps to `dataset`; filters become `json_extract(native,'$.f') = ?`; `text` becomes a substring match; echoes `sysparm_query` | view, rewound | 20 rows, 3k |
| `search_knowledge` | `chunk_fts MATCH` with every token quoted, so model text cannot cause syntax errors; `bm25(chunk_fts, 3.0, 1.0)`; `snippet()` | notes visible only once written | k ≤ 5, 600 chars |
| `get_topology` | alias lookup (every candidate, with its method), then one hop over `edge`; joins `sys_user.active` | rewound if audited | 40 edges |
| `get_oncall` | on-call rows with `start <= now < end`; policy levels and timeout | interval | 0.5k |
| `read_chat` | messages by channel, time and thread; mentions resolved; `reply_count` recomputed from visible replies | view | 60 messages |
| `check_status_page` | the latest snapshot with `page.updated_at <= now` | version window | 0.5k |

Pasted `pg_stat_activity` text stays in its Slack message and journal note for the agent to read; parsing it is the universal parser PLAN section 10 rules out.

**Failure handling** is the draft's section 7, unchanged: unparseable lines are kept and flagged, a missing source says so, unknown fields go to `attrs`, and ingest stops only on an unreadable bundle. `ingest_report` feeds a data-health strip in the UI.

## 6. Test plan (no model)

All pytest; the only code that reads `_internal/manifest.jsonl`. A static check and a runtime path guard stop `smalls/` opening `_internal/` or `scenario/` or importing `smalls_data`.

1. **Handle parity.** The manifest's handles equal the store's handles plus `handle_alias`, minus a listed exclusion set. Key on `(handle, version)`; one handle may carry several classes.
2. **Time parity.** `t_event` equals `t_utc` to the millisecond; `t_known` and the version windows match.
3. **Bytes.** `native` equals the bytes at `path` and `locator`.
4. **Folding.** Each multi-line manifest record is exactly one row, including the truncated record and its orphan frame.
5. **Clock round trip.** Rendering `t_event` back through `integration.yaml` reproduces `t_native` for every source with a zone rule.
6. **Visibility parity.** At each checkpoint, visible counts per source match the manifest under the same predicate.
7. **Leak test.** Every tool at its widest window returns nothing at or after `now`, and a future handle answers the same as an unknown one.
8. **Defects.** Duplicate: one row, two resolving handles. Truncation flagged. Fire-and-resolve keeps both transitions. Empty ServiceNow fields counted.
9. **Rewind.** ServiceNow fields at each checkpoint equal the manifest version valid then.
10. **Aliases.** Every resolved alias is correct (precision 1.0). Recall is printed, not asserted. Its author never opens the oracle.
11. **Caps.** Every tool stays under its cap, and every truncated result carries a footer.
12. **Golden queries.** Expected values come from the manifest at test time, e.g. the first `search_logs` row for a class is its earliest visible manifest row.
13. **Determinism.** Two ingests give the same `iterdump()` hash.
14. **Retrieval eval (committed).** 25 queries labelled by hand from the development bundle's served documents. Report recall@5 and MRR for BM25, and for hybrid if it is tried.
15. **Graph and similarity.** A two-hop traversal returns every edge exactly once, with its source. The similar-incident breakdown is deterministic, and every category score traces to rows in the store.

## 7. Stack, effort and build order

**Stack.** Python 3.12, stdlib `sqlite3` and `zoneinfo`, plus pydantic and pyyaml, which are already dependencies. No numpy, vectors or runtime downloads. One adapter per source in `smalls/tools/ingest/<source>.py`, about 30 to 80 lines each.

| Block | Raw h | ×1.5 |
|---|---|---|
| 6a: store, view, loader, path guard; `integration.yaml`, clocks, folding, handles | 1.75 | 2.6 |
| 6a: four log adapters and `search_logs` | 1.25 | 1.9 |
| 6a: Datadog, Dynatrace and Splunk-alert adapters with `list_alerts`; metrics with `query_metrics` | 1.25 | 1.9 |
| 6a: Slack and Confluence with `read_chat` and `search_knowledge`; `get_record`; parity and leak tests | 1.25 | 1.9 |
| **6a subtotal (unblocks the agent)** | **5.5** | **8.3** |
| 6b: Jenkins and ServiceNow, `list_changes` and `itsm_query`, with rewind | 1.5 | 2.25 |
| 6b: CMDB and aliases with `get_topology`; PayCore; ingest report; cap tests | 1.0 | 1.5 |
| Steps 7 and 9: PagerDuty with `get_oncall`; nginx, F5 and SolarWinds | 1.25 | 1.9 |
| **Core total** | **9.25** | **about 14** |
| Retrieval package (section 3a): index, eval, two-hop topology, similarity | 4.5 | 6.75 |
| **Total** | **13.75** | **about 20.5** |

**Build order, as PLAN section 6, step 6:**

1. `smalls/tools/store.py`: schema, `clock`, `visible_record`, loader registry, path guard, `--dump`.
2. `smalls/tools/ingest/common.py`: the `integration.yaml` reader, clock rules, the `break_only_before` splitter, and handles via `smalls.contracts.handles`. Write test 1 on logs first.
3. Log adapters, then `search_logs` (rows, stats, templates, SPL echo, caps).
4. Datadog, Dynatrace and Splunk-alert adapters with derived state and dedupe, then `list_alerts`; metrics, then `query_metrics`.
5. Slack and Confluence (plus closed-incident chunks once they exist), then `read_chat`, `search_knowledge` and the document index the harness injects. Write the 25 eval queries here, while the corpus is fresh.
6. `get_record`, the `smalls tools call` CLI, the leak test at 14:40 and 14:52, and tests 2 to 8. **The agent loop can start here.**
7. Jenkins and ServiceNow, then `list_changes` and `itsm_query` with rewind.
8. CMDB, aliases and mention edges, then `get_topology` with `hops<=2` and `near=` on `list_changes` and `list_alerts`; PayCore, then `check_status_page`; the ingest report; cap tests. **Gate B.**
8b. Similar-incident scoring per category in `search_knowledge`, once the closed-incident corpus exists (step 7); run the eval and decide on hybrid search.
9. In steps 7 and 9: PagerDuty and `get_oncall`, then nginx, F5 and SolarWinds.

## Outstanding after this doc

Research for ingestion, retrieval, scale and evals is complete. What is still open, and who owns it:

1. **Ruling needed:** the size-ceiling exemption for the scale render (section 3b). Data fix 10, the rewritten triggers, was signed off on 2026-10-13 (`docs/decisions/step6-decisions.md`).
2. **Measured during the build, not researchable now:**
   - tokens and cost per run, which size the eval matrix (section 3c);
   - the approaches results and the retrieval-check numbers for the write-up paragraph in section 3;
   - whether FTS5 and extension loading work on macOS with uv's Python (Linux verified).
3. **Not covered here, and the next research piece:**
   - the agent itself: loop, prompt, investigation state and memory, report schema use, and UI (PLAN section 13 is a stub);
   - this doc only fixes the tool and retrieval surface the agent will call.

   This session has seen answer-level material (see the chat log), so the agent prompt and loop should be planned by a fresh session that follows the held-out rule.
4. **Kept for the cut order, not cut now:** held-out h2, the step 13 stretch, and the S4 and S5 spikes.

## Contradictions to rule on

1. **Ten or eleven tools.** PLAN says ten; its section 4 table lists eleven. Keep `check_status_page` (a real responder step) and say eleven, or fold it into `get_record psp:<provider>`.
2. **Step 6 hours.** PLAN allows 1.5 h; realistically 8.3 h to the agent and about 12 h to Gate B. The agent starts near focused hour 17, not 9, unless 6b overlaps agent work. PLAN's hour-18 decision point needs revisiting.
3. **`saved_search=`.** Settled by C3: saved searches are records returned by `get_record`, never run.
4. **Where zones live.** PLAN 3.5 puts them in unserved `facts.yaml`; `docs/schemas.md` says "recorded with the bundle"; decision 9 needs code to convert. Decision 2 settles it.

5. **Evals move earlier (section 3c).** This adds about 6.5 corrected hours across steps 6 to 10. Ruling: **nothing is cut for now**, and the cut order is revisited only if time runs short. If it is, h1 stays, because nothing else evidences generalisation. The scale render still needs a written exemption from the size ceiling, since it is never committed or served as the bundle.

## Proposed edits to other docs

Each edit applies only if its decision is accepted.

- **PLAN 0:** drop rank-bm25 from the agent-phase additions; set the tool count per contradiction 1.
- **PLAN 2:** list `integration.yaml` as served config, not as a vendor source.
- **PLAN 3.2:** add `"t_known": null` to the manifest example, defined as "first time an observer could see this version; derivable from served bytes; null means `t_utc`".
- **PLAN 3.4:** visible means `coalesce(t_known, t_utc) < now`; metric points become visible when their bucket closes; a not-yet-visible handle is indistinguishable from an unknown one.
- **PLAN 3.5:** `facts.yaml` zones render into `integration.yaml`; the scenario YAML gains an optional `known_at`.
- **PLAN 4:** `search_knowledge` uses "SQLite FTS5 bm25()" and gains an `incident` mode with per-category scores; `get_topology` gains `hops<=2`; `list_changes` and `list_alerts` gain `near=` and `hops=`; the harness injects a document index; `group_by` gains `template`; `saved_search` returns the definition only; add the footer rule.
- **PLAN 6:** replace step 6 with 6a and 6b, using the hours and order above.
- **PLAN 8:** add `smalls/tools/store.py`, `smalls/tools/ingest/` and `bundles/<b>/integration.yaml`.
- **PLAN 9:** invariants 5 and 10 use `coalesce`; invariant 8 re-derives through `integration.yaml`; add "`t_known >= t_utc`, derivable from the served record".
- **PLAN 10:** add "a vector store, a graph database, an extension downloaded at runtime".
- **`smalls/contracts/manifest.py`:** add `t_known: str | None = None`, validated as UTC and placed in the `to_json` order.
- **HANDOFF.md** marks the research done; **DATA.md** records decisions 1 and 2 when implemented
