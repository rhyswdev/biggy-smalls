# Step 6a decisions: ingestion and tagging

Ruling record for step 6a, written on 2026-10-13 on branch `step6a-ingestion`. Step 6a is now **ingestion**: every source in the bundle is parsed from its messy vendor-native shape into the normalised in-memory store, with the replay clock, and with a tagging pass that gives the tools facets to filter and group on. The eleven tools follow as the next step (the former 6a tool items in `docs/PLAN.md` section 6), reading only this store.

A fresh session should be able to build the step from this file plus PLAN. Where this record differs from `docs/research/ingestion-and-retrieval.md` or `docs/research/tagging.md`, or from `docs/decisions/step6-decisions.md` on ingestion, this record wins. Everything here is decided; section 7 lists the rulings on the points that were open, and one that is deferred. No held-out path was read to write it, and it contains no held-out content.

The step is built (`docs/HANDOFF.md`; judgment calls and measured numbers in `docs/DATA.md` 98 to 126). Section 4 was amended on 2026-10-13, after the first `make tag` run and a round of candidate experiments: `act` is asked as one yes/no question per option, `signal` is asked both pick-one and as yes/no and also covers metric series, and the floor rule has a tolerance (section 4.7).

## 1. Decisions

The step adds two things to the existing plan (compose and tagging), fixes the store contract that no doc had stated, and declines one thing we costed (a compose database).

| Point | Ruling | Requirement served |
|---|---|---|
| I1 Store | **Unchanged: ruling D3 stands.** In-memory stdlib SQLite, rebuilt at every start. It is a library inside the Python process, so there is no database server and nothing to containerise. A compose database was costed and not adopted (section 5); Postgres stays what the research already called it: the production path. | "Clean enough code to understand and extend"; "Whether to use a vector database, knowledge graph, local files, database, or in-memory state" ("Make the tradeoffs you think are right") |
| I2 Compose | **New.** A `Dockerfile` and a `compose.yaml` with one service, the app. The UI and the agent service join the same file later. There is no database service because the store is in process. `uv` on the host stays the primary path (section 2). | "Instructions to run the system locally"; "Build something runnable and reviewable" |
| I3 Store contract | **New: closes the gaps in the adopted design.** No DDL existed, and several rules ingestion needs were written nowhere. The DDL and the ten rules in section 3 are now the contract. Each rule is proved by the parity test against the manifest, which only tests may read. | "Accept messy operational inputs"; "Explain its reasoning using evidence from the provided inputs" |
| I4 Tagging | **New, adopted as a deviation.** A closed-set classifier (jev) labels records in a separate `make tag` step; results live in a committed cache; startup ingest only reads the cache. This reverses "No model, no disk cache" at ingest and "a model parsing logs at ingest" as theatre at this size (`docs/research/ingestion-and-retrieval.md`, summary and section 3), and adds a model beside the agent design's "No extra models" (`docs/research/agent-design.md`, axis 9 and section 8.1). We keep the substance of both: no generative model touches ingestion, nothing a model writes is evidence, and the startup build stays deterministic and offline. What changes is narrow, and it is bound by rules T1 to T10 (section 4.3). | "Identify meaningful signals from noisy evidence"; "where AI is useful, where deterministic code is better"; "Useful agent behavior, not agent theater" |

## 2. Compose (I2)

- `Dockerfile`: Python 3.12 slim base and `uv`, both pinned by tag; `uv sync --frozen`; the virtualenv lives outside the source mount (`UV_PROJECT_ENVIRONMENT=/opt/venv`) so a bind mount of the repo does not collide with a host `.venv`; non-root user.
- `compose.yaml`: one service `smalls`, `build: .`, the repo bind-mounted at the working directory, `env_file` pointing at `.env` with `required: false`.
- `.dockerignore` excludes `.env`, `.env.*`, `.git`, `.venv`, caches. No secret is ever an `ARG`, an `ENV` or a copied file in the image. `JEV_API_KEY` is only needed by `make tag`.
- `uv` on the host stays the primary path. The container is the "only Docker installed" path: `docker compose run --rm smalls make test`.
- Rule recorded for later: services live in `compose.yaml`. There is no database service because the store is in process; if a later step needs a server (a UI, an agent service, a database), it arrives as a compose service.

## 3. The store contract (I3)

Layout is unchanged from PLAN section 8: `smalls/tools/store.py` and `smalls/tools/ingest/`, so the held-out rule in CLAUDE.md already covers it. New: `smalls/tools/ingest/tagging/`, `smalls/cli.py`, `tags/cache.jsonl`, `eval/tags/labels.yaml`.

### 3.1 DDL

No DDL existed. We fix it now. The implementer may add indexes, and may adjust column types if the parity test demands it, recording the change in `docs/DATA.md`.

```sql
CREATE TABLE record (
  rid          INTEGER PRIMARY KEY,
  handle       TEXT NOT NULL,          -- minted only through smalls/contracts/handles.py
  version      INTEGER NOT NULL DEFAULT 1,
  source       TEXT NOT NULL,          -- the integration.yaml sources: key
  dataset      TEXT NOT NULL,          -- the file-level set inside the source (incident, events, log, ...)
  kind         TEXT NOT NULL,          -- log, alert, change, build, commit, incident, note, message, doc, status, config, ...
  native_id    TEXT,
  host         TEXT,                   -- from the Splunk host rule; differs from the handle's file-stem host for F5 and k8s
  entity_raw   TEXT,                   -- unresolved; alias resolution arrives with get_topology in step 7
  entity_id    TEXT,
  stream_key   TEXT,                   -- alert stream; Splunk deliveries share their sid
  t_event      INTEGER NOT NULL,       -- epoch ms UTC
  t_end        INTEGER,
  t_known      INTEGER NOT NULL,       -- epoch ms UTC; 0 for the records with no time in their bytes
  valid_from   INTEGER,
  valid_to     INTEGER,
  t_native     TEXT,                   -- the time as the vendor wrote it
  tz_rule      TEXT,                   -- zone and format used, from integration.yaml
  sev          INTEGER, sev_text TEXT, status TEXT,
  title        TEXT, body TEXT, parent TEXT,
  template_id  TEXT,                   -- logs only: h8 of sourcetype and masked first line
  keep_1_in    INTEGER, sample_per TEXT,   -- logs only, from the served sampling: block; the class name is never stored in a showable column
  tag_key      TEXT,                   -- content hash of what the tagger saw; many records share one
  tags         TEXT NOT NULL DEFAULT '{}',   -- JSON {facet: value}, after thresholds
  attrs        TEXT NOT NULL DEFAULT '{}',   -- JSON, parsed fields that are safe to show
  native       TEXT NOT NULL,          -- the exact served bytes of the record
  path         TEXT NOT NULL, locator TEXT NOT NULL,
  flags        TEXT NOT NULL DEFAULT '{}',   -- JSON: withheld field paths, DST fold, truncated line, ...
  UNIQUE (handle, valid_from, locator)
);
CREATE TABLE point (series TEXT NOT NULL, t INTEGER NOT NULL, value REAL, interval_s INTEGER NOT NULL,
                    t_known INTEGER NOT NULL, path TEXT NOT NULL, locator TEXT NOT NULL);
CREATE TABLE chunk (rid INTEGER NOT NULL REFERENCES record(rid), seq INTEGER NOT NULL, heading TEXT, text TEXT NOT NULL);
CREATE VIRTUAL TABLE chunk_fts USING fts5(heading, text, content='chunk', tokenize='porter unicode61');
CREATE TABLE tag_answer (tag_key TEXT NOT NULL, facet TEXT NOT NULL, value TEXT NOT NULL, p REAL NOT NULL, chosen INTEGER NOT NULL);
CREATE TABLE ref (source TEXT NOT NULL, name TEXT NOT NULL, native TEXT NOT NULL);   -- files with no manifest rows: Slack users and channels
CREATE TABLE clock (now INTEGER NOT NULL);   -- zero or one row; empty means nothing is visible
CREATE VIEW visible AS
  SELECT r.* FROM record r, clock c
  WHERE r.t_known < c.now
    AND (r.valid_from IS NULL OR r.valid_from <= c.now)
    AND (r.valid_to   IS NULL OR c.now < r.valid_to);
```

Plus `visible_point` and `visible_chunk` on the same predicate. An empty `clock` makes every view return nothing, so a forgotten clock fails closed. No tool reads `record`, `point` or `chunk` directly (a test checks it).

How this sits with D3, which stands: `record`, `point`, the FTS5 porter table (`chunk` with `chunk_fts`) and the `visible` view over `clock` are D3's. `tag_answer` and `ref` are new with this step. The two extra views apply D3's one predicate to the two other tables a tool reads. The clock holds zero or one row rather than always one, so that an unset clock shows nothing.

### 3.2 The ten rules

These are rules the docs did not state. Each is proved by the parity test against the manifest, which only tests may read.

1. `t_event` is the manifest's `t_utc`, derived per source from served bytes. Observed rules to implement and confirm: versions keep the first version's event time; a Splunk alert uses the epoch in its `sid`; a metric point uses its bucket start; a k8s event uses `firstTimestamp`; a change request uses `work_start`, else `start_date`. If any row's `t_utc` cannot be derived from served bytes, stop and report; never special-case by id.
2. `valid_from` is the version's `t_known`; `valid_to` is the next version's `valid_from`; the last version's `valid_to` is null. Version order comes from the file suffix. The manifest is the judge where a first version has a null `valid_from`.
3. Six records carry no time in their bytes (2 Datadog synthetics, 4 PagerDuty escalation policies). They get `t_known = 0` and are visible at every checkpoint; the parity test asserts that instead of comparing times.
4. Visibility is `t_known < now` for every table, metric points included (the research's `<=` for points is superseded by PLAN 3.4).
5. **Versions, not rewind.** The live ServiceNow incident (15 snapshots) and PagerDuty incidents are version snapshots like Dynatrace problems, Jenkins builds and PayCore. `sys_audit` rows are records in their own right, not a rewind mechanism. Change requests are served as exported and are visible from creation (`docs/DATA.md` 35); there is no rewind for them either, so the store leak test in section 6 is the check that an exported change carries nothing later than `now`, and a failure there is fixed through the generator, never in an adapter. Derived at `now`: Datadog monitor state, PagerDuty service status, Slack thread summaries, SolarWinds active state. The counts PLAN 3.4 recomputes at `now` (Slack `reply_count`, Dynatrace `evidenceDetails.totalCount`) stay as PLAN says.
6. **Fields whose served value is not true at `now` are withheld by ingestion.** Each adapter lists the JSON paths in its records that hold them: the 13:45 sync values (Datadog `overall_state`; PagerDuty service `status`; `docs/DATA.md` 93) and the 15:30 export values (Slack `reply_count`, `reply_users`, `reply_users_count`, `latest_reply`; Slack channel `members`; the metric envelope's `to_date`, `series[].end`, `series[].length`). `attrs` never contains them, `flags.withheld` names them, and one store function renders `native` without them. Ingestion owns what is safe to show; the tool that shows a record owns the value derived at `now`.
7. A log record's sampling rate comes from the served `sampling:` classes, first match on the first line; a Postgres `connection received` line takes the class of the session with the same pid. Only `keep_1_in` and the unit are stored for showing; class names and patterns never reach a showable column.
8. Stack traces come in two shapes (continuation lines in the payments logs, a `stack_trace` JSON field on checkout-api records); both land in `body` the same way so "folded to 3 frames" works on either.
9. The regex masker ruled in D6 runs at ingest and writes `template_id`, because tagging is per template. `search_logs group_by=template` later groups on the stored column; D6's behaviour is unchanged.
10. `smalls/cli.py` with `[project.scripts] smalls = "smalls.cli:main"` arrives now with `smalls ingest` (build the store, print the ingest report, optional `--dump x.db`) and `smalls tag`. `smalls tools call` arrives with the tools.

## 4. Tagging (I4)

The research behind this section, with its sources, is `docs/research/tagging.md`.

### 4.1 What the classifier is and the cookbook we follow

Jev is a hosted classifier from TypeSafe. It generates no text: a call sends a `state` and typed questions and gets back, per question, one option from a closed set with a probability for every option (`Choice`), a yes probability (`Noul`), or a position on an ordered scale (`Score`).

We base the pass on the **Twitch chat filter** cookbook (https://docs.typesafe.ai/cookbooks/twitch_chat_filter): one request per short record, a category `Choice`, a disk cache, bounded concurrency, and the decision rules in code. What we change from the cookbook:

- the key is passed explicitly from `JEV_API_KEY` (the SDK reads a different variable);
- the cache key includes the pinned classifier version and the profile version;
- the cache keeps the full raw probabilities, so thresholds can change with no new calls;
- every `Choice` has an `other` option and a probability floor.

Facts that shape the design: the classifier is not deterministic and has no seed (two identical requests differed by 0.02 on one answer), so the committed cache is the only source of reproducibility; cost and limits are irrelevant at our size (about 315 requests, about a cent); there is no batch endpoint; it is weak on numbers, counting and encoded text, and leans toward the first option of a `Choice`; the vendor states it does not train on inputs but gives no retention period (the bundle is synthetic).

### 4.2 Why this is a deviation, and why we take it

The research said "No model, no disk cache" at ingest and called "a model parsing logs at ingest" theatre at this size; the agent design ruled "No extra models". We keep the substance of both: no generative model touches ingestion, nothing a model writes is evidence, and the startup build stays deterministic and offline. What changes is narrow: a classifier assigns labels from a closed vocabulary, code validates every label, and the labels are query facets, never evidence.

Requirement served: "Identify meaningful signals from noisy evidence", and the Final Note's "where AI is useful, where deterministic code is better". The guard against agent theatre is measurement: tag quality on a hand-labelled sample, and an ablation of the agent with tags on and off. Tags are off by default (ruling O2), so the ablation reports what turning them on buys, and the write-up says so either way.

What a tag adds that the data lacks: log lines have a level and a logger but no vendor-neutral meaning, so the agent has to know vendor strings to ask for "things running out"; alert signal type is a field only in Dynatrace; ServiceNow `subcategory` is empty on every incident; chat has no field at all for whether a message is an observation or a theory.

### 4.3 Rules T1 to T10

| Rule | Ruling | Requirement served |
|---|---|---|
| T1 Own bytes only | A tag is a pure function of one record version's own text, the profile version and the pinned classifier version. No neighbouring records, no bundle summary, no corpus statistics in the request. A tag is therefore visible exactly when its record version is, and the same text gets the same tag in any bundle. | "Maintain some form of evolving investigation state or memory"; "Grounded reasoning based on evidence" |
| T2 High confidence or nothing | A tag is applied only when the classifier is confident: the chosen value is an option of its facet, is not `other`, and has a probability at or above the floor (section 4.5). Anything else stores nothing for that facet, so an untagged record is the normal outcome of doubt, and a wrong tag is the error we pay to avoid. A yes/no question, if one is ever added, stores only a confident yes; a no or an unsure answer stores nothing. Code applies the rule, never the classifier. | "Tradeoffs between simple code and agentic behavior"; "Good handling of ambiguity and uncertainty" |
| T3 Annotate, never overwrite; never evidence | Tags sit beside native fields and are shown as machine labels. Handles cite records; a tag cannot be cited and the scorer ignores tags. | "Evidence grounding" |
| T4 Never a default filter | A tool filters on a tag only when the agent asks, and its footer then says how many rows matched without the tag filter and how many of those are untagged, so a wrong tag cannot silently hide evidence. | "Good handling of ambiguity and uncertainty"; "Failure handling" |
| T5 Offline by construction | Startup ingest reads `tags/cache.jsonl` and never imports the classifier SDK; a cache miss leaves the record untagged and is counted in the ingest report. Only `make tag` calls the API. A test asserts the committed cache covers the committed bundle and that ingest opens no socket. | "Build something runnable and reviewable"; "Instructions to run the system locally" |
| T6 A config flag, off by default | `SMALLS_TAGS=on\|off`, default `off` (ruling O2). Off gives the store and tools exactly as PLAN specified them before this step; on loads the cached tags and adds the tag inputs to the tools. The two values are the ablation arms. | "Make intentional decisions and be prepared to explain them" |
| T7 No bundle strings | Profiles (instructions and option descriptions) are prompt text and fall under the bundle-string lint, which does not exist yet and is built in this step. | "Grounded reasoning based on evidence" |
| T8 Frozen with the agent | Profiles live under `smalls/tools/`, so they fall under the held-out rule and the `agent-v1` freeze tag. | "Ability to move fast without producing chaos" |
| T9 Facet counts over visible rows only | The option names are the same in every bundle, so showing them leaks nothing; counts per option are computed through `visible`. | "Maintain some form of evolving investigation state or memory" |
| T10 The pinned classifier version is a config value | It lives in `smalls/tools/ingest/tagging/config.toml` and nowhere else; code, profiles and docs say "the pinned classifier version". This is the model-agnostic rule applied (ruling O3): changing the classifier is one config line and a `make tag` run. | "Clean enough code to understand and extend" |

### 4.4 Taxonomy v1

Three facets (ruling O5, amended in section 4.7). `change_kind` is one `Choice`, with options in a fixed order and `other` last; `act` is one yes/no question per option; `signal` is asked both ways, a `Choice` and a yes or no per option, in two requests. A text can therefore carry more than one `signal` or `act` value. Facets come from vocabularies that predate this incident: the four golden signals of monitoring plus availability and change; ordinary speech acts in an incident channel; standard change types.

| Facet | Options | Applies to | Units here |
|---|---|---|---|
| `signal` | latency, errors, saturation, availability, traffic, change, security, routine, other (asked pick-one, and again as a yes or no each) | log templates; alert definitions and titles; incidents; metric series (pick-one only, the first five options) | 100 templates; 42 alert texts; 23 incidents; 14 series |
| `act` | observation, hypothesis, action_taken, action_proposed, question, status, social (a yes or no each, so no `other`) | human chat messages; ServiceNow journal entries; PagerDuty notes | 81 + 28 + 1 |
| `change_kind` | release, rollback, config, infra, network, data, job, other | change requests; Jenkins builds; commits | 15 + 15 + 9 |

A request sends one record's own text and the questions of the one profile asked about that kind of record. A pick-one question carries the whole option list with one line of meaning per option, and nothing else; a yes/no question carries one option's line. The working meanings, from which the profile text is written:

| Facet | Option | Meaning |
|---|---|---|
| `signal` | latency | Something is slow: response times, durations, waits, slow queries. |
| | errors | A request or operation failed: exceptions, error codes, rejected calls. |
| | saturation | A finite resource is full or running out: pools, queues, connections, threads, memory, disk, quotas. |
| | availability | Something is down, unreachable, unhealthy, restarting or failing over. |
| | traffic | The amount of work arriving changed: request rate, volume, throughput. |
| | change | Something was deployed, configured, started, stopped or scheduled on purpose. |
| | security | Authentication, authorisation, certificates, access or audit. |
| | routine | Normal operation with nothing wrong: a request served, a heartbeat, a job finished. |
| `act` | observation | Reports something seen, with or without data: a number, a graph, a log line, a symptom. |
| | hypothesis | Proposes or doubts a cause. |
| | action_taken | Says something has been done. |
| | action_proposed | Suggests, requests or agrees what to do next. |
| | question | Asks for information. |
| | status | Sums up where things stand for others: impact, progress, handover. |
| | social | Greetings, thanks, acknowledgements; nothing about the substance. |
| `change_kind` | release | A new version of application code is built or deployed. |
| | rollback | A return to an earlier version or setting. |
| | config | A setting, flag, limit or parameter changes with no new code. |
| | infra | Hosts, containers, clusters, capacity, operating system or platform components. |
| | network | Firewalls, load balancers, DNS, routes. |
| | data | Schemas, migrations, indexes, data fixes, backups, restores. |
| | job | A scheduled or batch job is added, changed or run. |

Every facet ends with `other` (none of the above, or cannot tell), which stores nothing.

Why the lists look like this:

- `routine` sits just before `other`, not first. The classifier leans toward the first option of a `Choice`, and a line that carries a signal but is tagged `routine` is the costly error, because a signal filter then hides it.
- `act` has seven options where the first draft had nine: `evidence` is merged into `observation` and `decision` into `action_proposed`. Each pair was the likeliest confusion, and a confused pair lowers confidence on both, which under T2 means fewer tags.

Left out of v1:

- `layer` (application, database, network and so on). Source, sourcetype and host are already columns, so filtering by where a record comes from is code's job. The other reading, the layer a text is about, is ambiguous to label by hand, and `signal` covers its main use. It is the first facet to add back if the ablation shows the agent failing to cross layers.
- `resolution` on closed incidents. There are 11; the agent reads 11 close notes in one call, so a filter over them adds nothing.

Bot posts get `act=automated` from the `bot_message` subtype in code, with no request. Log lines are never tagged one by one: the template is the unit, which also survives the scale render (about 75,000 lines, a few hundred templates). Identical texts share one cache entry, so the 15 versions of the live incident cost one request per distinct text. Not tagged in v1: knowledge pages (8 pages; the source inventory already lists every title), CIs, users and groups. A metric series is tagged once, from its query, and the tag sits on each of its points.

Where the tools use them when tags are on (the contract for the tool step): `search_logs` gains `signal?` and `group_by=signal` (a line with two values counts under both); `list_alerts` gains `signal?`; a `signal` or `act` filter matches a row that carries that value among its values; `read_chat` gains `act?`; `list_changes` gains `change_kind?`; `itsm_query` gains `signal?` on incidents; the source inventory gains counts per facet over visible rows and lists each metric series with its `signal`.

### 4.5 Thresholds, cache and commands

- **The floor.** A value is applied only when its probability is at least the floor (T2): the chosen option's for a `Choice`, the yes for a yes/no question. The floor starts at 0.8 and is set for precision, not coverage: the final number is the lowest value at or above 0.8 at which, on the hand-labelled sample, at least 9 in 10 stored values match the label and no stored value flips between repeats. A value flips when a repeat brings it back more than the tolerance under the floor (section 4.7). The measured floor is 0.90 (`docs/DATA.md` 121). The cache keeps the raw probabilities, so moving the floor needs no new calls.
- `tags/cache.jsonl`, committed, outside `bundles/`: one line per `tag_key = sha256(classifier version, profile id and version, canonical JSON of the state)`, holding the raw answers, the answering version and token usage, and no record text. Sorted by key for stable diffs.
- `make tag`: builds the store, collects cache misses, calls the classifier eight at a time with the SDK's retries, appends the answers, prints requests, tokens and cost. `make ingest` and every test run with no key.
- The SDK (`typesafe-sdk`, pinned below the next minor) sits in an optional `tagging` dependency group that only `smalls tag` imports.

### 4.6 How tag quality is measured

- **Labels first.** `eval/tags/labels.yaml`: units stratified across the facets (134 as built: 80, 34 and 20), labelled by hand from the record text and the option definitions, and committed before the classifier answers the question they are scored against, so git proves the order.
- **Precision and coverage.** Per facet, two numbers: of the tags applied, how many match the hand label (the bar is 9 in 10), and how many labelled units got a tag at all (reported, no bar). The confused pairs are listed. A facet that cannot reach the bar at any floor is stored but not offered as a tool filter, and the write-up says so.
- **Stability.** Three cold repeats over the labelled sample, one of them with option order shuffled: count the applied tags that flip, and raise the floor until none does.
- **Ablation.** Once the agent runs (step 6c): the regression set (arm C, 14:52, 3 runs) with `SMALLS_TAGS=on` and `off`. Report outcome score, fabricated and unseen citations, tool calls and tokens to the first correct leading hypothesis.

### 4.7 Amended after the first run

The first `make tag` run used the taxonomy exactly as ruled, and its numbers are kept (`docs/DATA.md` 121). We then tried six candidates on a second, blind-labelled sample (`docs/research/tagging.md` section 7) and ruled on what they showed.

| Point | Ruling | Why |
|---|---|---|
| `act` form | **One yes/no question per option**, where the first run asked pick-one. A message's `act` tag is a list. | A message often does two things (reports and proposes), and pick-one split its probability and stored nothing: at the final floor pick-one tags 43 of 110 units and yes/no tags 86. This is rule T2's "yes/no question", stored only on a confident yes. |
| `signal` on metric series | **Adopted.** A series is tagged once from its query, with the five golden-signal options, and the tag sits on each of its points. | A query names what it measures; 13 of 14 series tagged, all matching their label, none moving between repeats. It lets the source inventory group series by signal. |
| `signal` form | **Asked both ways**: the pick-one question as first ruled, and one yes/no question per option; the tag is every value either stores, a list. The yes/no `security` line reads "Who is allowed to do what". | Pick-one left the most diagnostic lines untagged, because a failed request that is also an exhausted pool splits its probability; yes/no tags them but rarely says yes to `routine`, so each form covers what the other misses. Adopted on a test (O6): 37 of 37 stored values matched on 45 units labelled first, and under the shipped profile 62 of 62. Log records with a `signal` go from 2,067 to 2,797 of 3,989. |
| The floor rule | **One floor for every facet, with a tolerance**: a repeat that brings a value back no more than 0.02 under the floor reproduces it. | The classifier rounds to 0.01 and moves by about that between identical requests, so some value always sits beside any threshold. Without the tolerance the rule gave 0.96, and on the final sample no floor at all, though at 0.90 the only misses are by 0.01 and 0.02; with it, 0.90, and any tolerance from 0.02 to 0.09 gives the same number, because the one real flip under 0.90 misses by 0.10 or more. We also considered a floor per facet and kept one number. |

Not changed: the option meanings of the pick-one questions, `change_kind` as pick-one, tags off by default, and every rule T1 to T10.

## 5. Considered and not adopted: a compose database

We costed a Postgres service under compose before ruling I1: PostgreSQL 18 in one pinned image, with the schema, a replay clock and the ranking run on this machine in a throwaway harness and torn down. It would have reopened problems the in-memory design does not have.

- **The replay clock.** A one-row `clock` table is only safe inside one process. On a shared server, a 14:40 run and a 14:52 run in the eval matrix would overwrite each other's `now` and leak the future, so the clock would need transaction-scoped settings, a stamp check against pooled connections, and a read-only role. Measured: a plain `current_setting` view fails open on a pooled connection. After an earlier user of the connection set the clock at session level, the next query, with no clock of its own, answered under the stale clock and returned 4,801 rows.
- **A bundle key.** Held-out variants and the scale render mint the same handles as the development bundle, so every table and view would need a bundle column or a separate instance. A development database with a held-out bundle loaded is also readable by any session, which the file-based held-out rule does not cover.
- **Staleness.** In memory was chosen because it removes cache invalidation. A persisted store brings back a fingerprint and a stale-store rule.
- **Ranking.** Core Postgres has no BM25; the ruled wording ("FTS5 `bm25()`") would need BM25 written in SQL or an extension with licence or maturity problems. Measured over 41 heading chunks of the eight served knowledge pages with 12 queries, against the ruled FTS5 `bm25()`: built-in `ts_rank_cd` gave the same top hit on 5 of 12, `ts_rank` on 7 of 12, BM25 written in SQL on 9 of 12. Of the three BM25 extensions, two carry an AGPL licence option, one of those has no tagged releases, and the permissively licensed one has no maintained image.
- **Byte-exact `native`.** JSONB does not keep the served bytes: keys are reordered, duplicate keys dropped and whitespace removed, so the payload would need a text column beside the queryable copy.
- **Clean clone.** Docker would become a prerequisite for 21 tests that run offline today, for the leak test and for Gate B.

Speed was never the objection: measured, the container was healthy 1.7 s after a cold start and a tool call through a connection pool took 1.9 ms at the median. Nothing in the tagging pass needs a server: the committed tag cache is the persistence. At 5,861 manifest rows the move buys nothing.

**One finding carried to `search_knowledge`.** It applies to SQLite too: with every query token quoted and implicitly ANDed, 9 of 12 natural-language test queries over 41 runbook chunks returned nothing. `search_knowledge` should join its quoted tokens with OR. This was measured in a throwaway harness; re-verify when `search_knowledge` is built. The 12 queries are ours and the reference was the ruled engine, so this is a sanity check, not a relevance benchmark.

## 6. Build order and acceptance

| # | Build | Accepts when |
|---|---|---|
| 1 | Compose and CLI skeleton: `Dockerfile`, `compose.yaml`, `.dockerignore`, `smalls/cli.py`, `make ingest`, `make tag`, `make docker-test` | `docker compose run --rm smalls make test` passes; the image contains no `.env` |
| 2 | Store: DDL, `clock`, the `visible` views, `integration.yaml` reader, loader limited to `sources:`, path guard, static import check, ingest report | An empty clock shows nothing; the loader refuses `_internal/`, `scenario/` and `triggers/`; nothing under `smalls/` imports `smalls_data` |
| 3 | Adapters for all 17 `sources:` keys, raw logs first, then Slack, Datadog (with `point`), Jenkins, ServiceNow, PagerDuty, Dynatrace, Splunk alerts and saved searches, Confluence (with `chunk`), SolarWinds, PayCore | **Parity: 5,861 of 5,861 manifest rows** match one store row on handle, path, locator, `t_utc`, `t_known`, `valid_from` and `valid_to`, with no extra rows; `native` equals the served bytes; the report shows zero unparsed lines |
| 4 | Visibility: versions, the fields withheld under rule 6, the store half of the leak test | Visible counts at 14:40 and 14:52 equal the counts the manifest gives under the same predicate; no showable field of a visible row carries a time at or after `now` beyond the lint's documented exemptions, or a future token; `make leak-test` is no longer a stub |
| 5 | Templates and sampling: the masker, `template_id`, `keep_1_in` | Every log record has a template; template count is stable across runs; two ingests give the same `iterdump()` hash |
| 6 | Tagging: profiles, the bundle-string lint, the labelled sample (committed first), `smalls tag`, the cache, thresholds, `SMALLS_TAGS` | With `SMALLS_TAGS=on`: zero cache misses on the committed bundle, every applied value in the vocabulary and at or above the floor, ingest opens no socket. With the default (off): the store holds no tags and passes the same parity. The precision, coverage and stability table and the final floor are in `docs/DATA.md` |
| 7 | Docs: `docs/HANDOFF.md`, `docs/DATA.md` entries for every judgment call, PLAN status line | A fresh session can start the tool step from HANDOFF alone |

Existing checks stay green throughout: `make data`, `make lint`, `make test` (21 tests today), all offline.

**Not in this step:** the eleven tools and `smalls tools call`; the result envelope and error contract; the source inventory; values derived at `now` (monitor state, thread counts, service status); alias resolution and the edge table (step 7); the agent port; the tags ablation (needs agent runs).

## 7. Rulings on the open points

Five points were open when this record was first written, and the candidate experiments opened a sixth. All are now ruled except O4, which is deferred.

| # | Point | State | Ruling or default |
|---|---|---|---|
| O1 | An extra set of yes/no labels on each change and commit saying what the change touches (calls to a dependency, resource limits, library versions, observability, feature flags, schema), so the agent could ask for changes by what they do. | **Ruled** | Left out. The list reads as chosen with this incident's answer in mind, which the held-out claim cannot afford, and `list_changes` already returns the whole day in one capped call; spike S4 covers the worded-differently case. |
| O2 | Do tags ship on? | **Ruled** | Off by default. `SMALLS_TAGS` defaults to `off` and `on` is opt in. The ablation still runs both arms and the write-up reports the numbers; making `on` the default would be a new ruling made on those numbers. |
| O3 | Does a pinned classifier version in a config file break the rule we had written as "no model identifiers"? | **Ruled** | No. The rule means model-agnostic design: the agent model and the classifier version are config values, and code, prompts and docs refer to them by role and assume no particular model. CLAUDE.md now says so, and T10 is that rule applied. |
| O4 | How held-out bundles get tags. A session bound by the held-out rule cannot read `eval/heldout/`. | **Deferred** | We settle it separately, outside this step. With tags off by default, held-out runs need no tags, so nothing waits on it. |
| O5 | The taxonomy itself: facets, option names, floor. | **Ruled, amended** | Three facets, `signal`, `act` and `change_kind`, with the options and meanings in section 4.4; `layer` and `resolution` are left out of v1. A tag is applied only at high confidence (T2), with a floor that starts at 0.8 and is set for precision (section 4.5). The profiles were read before the first `make tag` and run as shown. Amended after that run (section 4.7): `act` is asked as yes/no per option, `signal` also covers metric series, and the measured floor is 0.90 with a tolerance of 0.02. |
| O6 | The other candidates tried: `signal` asked as yes/no beside pick-one; a `resource` facet (cpu, memory, disk, network, pool) and a `fault` facet (timeout, unavailable, exhausted and so on); `layer`. | **Ruled** | `signal` as yes/no is adopted (section 4.7), on a test set in advance: at least 9 in 10 stored values matching on a larger sample labelled first, which it met with 37 of 37 on 45 units, followed by a read of every value it adds. `resource` and `fault` are left out: they are precise and sparse, and their busiest values are this incident's own mechanism, the concern of O1. `layer` is left out: it mostly restates the source. The numbers are in `docs/research/tagging.md` section 7 and `docs/DATA.md` 124. |

## 8. Carried to the tool step

- `search_knowledge`: join quoted tokens with OR (section 5).
- The values derived at `now` listed in rule 5 of section 3.2, each with the tool that shows it.
- The leak test's tool-output half and the derived-state assertions in HANDOFF.
- The tag parameters and footer in sections 4.3 (T4, T9) and 4.4.
- Stale numbers to refresh when measured: "141 ms" and "about 150 ms" (rebuild time, measured before the full bundle), "4,038" and "4,554" manifest rows (now 5,861).
