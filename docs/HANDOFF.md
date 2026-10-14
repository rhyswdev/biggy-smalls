# Handoff: where the data phase stands

Updated 2026-10-14, after the first live run: the eleven tools, the agent, the verifier, carry-forward, the matrix runner and the terminal are built and tested offline, on branch `agent-build`, not pushed. One live run has been made and committed (row C3), and `make demo` replays it. The live shakedown (row C4) is next, and it needs a go-ahead.

## Done
- Step 0: Python 3.12 uv skeleton, packages `smalls` and `smalls_data`, Makefile targets.
- Step 1: handle grammar and function, manifest contract, `scenario/facts.yaml` with 10 people, 2 integration users, 7 groups.
- Step 1b: vendor schema extraction and the schema conformance lint, with minimal-record tests.
- Step 2: `scenario/larkspur-checkout.yaml`, 74 timeline rows, connection budget, curves, log windows and sampling.
- Every judgment call made so far is recorded with its reason in `docs/DATA.md` (23 entries).

## Done since Gate A
- Gate A passed. Visibility on `t_known` and a served `integration.yaml` (`docs/DATA.md` 24, 25); the research doc is `docs/research/ingestion-and-retrieval.md`.
- Steps 3 to 5 for the slice: `smalls_data/gen/` (one module per source plus `model.py`, the shared request stream), `smalls_data/lint.py` (invariants 1 to 5, 7 to 9, 11 to 13), hand-written content under `scenario/content/`, worker chatter for both Slack channels (reviewed). `make data`, `make lint` and `make test` pass; the build is deterministic. Decisions are `docs/DATA.md` 26 to 40.
- Slice counts: payments 1,914 lines (target 1,700), checkout-api 1,175 (1,300), Postgres 209 (180), k8s events 57 (60); 84 structured records (full-bundle target 210); 8 vendor sources.

## Research and rulings: done
- Ingestion and retrieval research is complete (`docs/research/ingestion-and-retrieval.md`), and the step 6 panel ruled on every open point (`docs/decisions/step6-decisions.md`).
- Both are merged into `docs/PLAN.md`: eleven tools over an in-memory SQLite store (section 4), the step 6 build order, eval layers from the first run, the approaches eval and the vector spikes (section 6), invariants 14 and 15 (section 9). PLAN is the single plan again.

## Open ruling
- **Scale render.** Whether `make scale` may render the same scenario with log sampling at 1:1 into a git-ignored folder, exempt from the size ceiling (invariant 13). Arms D and E of the approaches eval (PLAN 6.1) need it; until ruled they are designed, not run.

## Step 6.0 data fixes: done
- Fixes 1 to 10 from `docs/decisions/step6-decisions.md` section 3 are in, plus invariant 14 and test hygiene, PLAN sections 1, 2, 3.2 and 9 and re-review corrections. Judgment calls are `docs/DATA.md` 41 to 71.
- Lint invariants added: 10 (served-file half: every file under a listed source; nothing visible at a checkpoint carries a later time), 14 (`smalls_data/parity.py`: every handle and `t_known` re-derives from served bytes), 15 (sampling: a binomial band per 10-minute bucket against `_internal/population.json`, and Spearman above 0.8 at the narrowest width a correct sampler passes, as agreed; per-minute is out of reach at the ruled rates). Invariant 7 catches authoring labels in records dated before the day.
- `make data` is deterministic from a clean checkout; `make lint` 0 errors; `make test` 19 passed (tests read the committed bundle and build into `tmp_path`).
- Slice counts: payments 2,005 lines (target 1,950), checkout-api 1,243 (1,300), Postgres 679 (650), k8s 57 (60); 3,984 of 5,000 lines with nginx (570) and F5 (20) still to come; 85 structured records of 250; 8 vendor sources of 11.
- SRE re-review (read-only, against the panel's SRE review): all ten fixes FIXED, fixes 1 to 3 fully. Data ready for step 6.

## Step 7 and step 9 data: done
- The bundle now holds everything the 14:40 and 14:52 agent tests need (`docs/DATA.md` 72 to 95), plus the cheap 15:15 items (PayCore v2 and v3, late customer incidents):
  - ServiceNow service model: 19 CIs and 18 relationships with the designed defects (Payments Gateway owned by inactive Marcus Okafor, Checkout API still depending on retired redis-sessions-01, no checkout-to-gateway edge, no PayCore CI), users and groups;
  - INC0048811 as 15 version snapshots with its journal and `sys_audit`; 11 customer incidents quoting real orders the logs keep; 8 closed noise incidents; 9 more changes;
  - PagerDuty services, policies, on-call, incidents 8810 to 8815 as versions, log entries;
  - Datadog search-indexer, ord-01 fire-and-resolve, marketing synthetic and success-rate monitors with series; Dynatrace P-26101377; F5 ltm flaps and the F5 alert; nginx edge log (per cart, joins checkout-api); SolarWinds; six more knowledge pages (KB0010502 is the retry design note);
  - `_internal/answer_key.yaml` for 14:40 and 14:52 with lint invariant 6.
- An SRE review (read-only) found the data ready for 14:40 and 14:52 testing, no critical findings; its five majors and most minors are fixed (DATA.md 93, 94); open minors are DATA.md 95.
- Budget: 4,815 log lines of 5,000, 242 structured records of 250, 10 vendor sources of 11. Trim before adding anything.
- `make data` is deterministic from a clean checkout, `make lint` 0 errors, `make test` 21 passed.

## Agent design and spike: done
- `docs/research/agent-design.md` holds the settled agent design, with its rulings in sections 7 and 8.5; `spikes/agent/` is a self-contained LangGraph spike with its own `pyproject.toml`, `uv.lock` and offline tests (`cd spikes/agent && uv sync && uv run --group dev pytest -q`); `.env` files are git-ignored and `.env.example` names the API keys.
- PLAN carries the rulings: report fields `do_not[]`, code-written `proposed_actions[]` and a structured `stakeholder_update` (3.1); `ic:note/<h8>` handles, classed with chat (3.2; `ic_note_handle()` and its grammar test are in `smalls/contracts/`); the A1 envelope split into `shown[]` and `referenced[]`, only shown handles citable (4); the scorer rule for shown and `ic:note` handles (5); section 13 points to the design doc. PLAN carries no time estimates: we track time and call cuts as needed.
- Next agent step: port the spike into `smalls/agent/` (build row 1 of `docs/research/agent-design.md` section 5) once the tools of step 6a-tools exist, starting from `search_logs`. Live latency and cost numbers come from the local session's shakedown (build row 2); until then they stay "not measured". The only open agent question is the API key (agent-design.md section 7, row 1).
- The agent, its prompts, `smalls/tools/` and the scorer fall under the held-out rule in CLAUDE.md; a session that has read answer material must not edit them or `spikes/agent/`.

## Step 6a ingestion: done
Built in the order of the build table in `docs/decisions/ingestion-decisions.md`, on branch `step6a-ingestion`. Judgment calls and measured numbers are `docs/DATA.md` 98 to 126.
- **Compose and CLI.** `Dockerfile`, `compose.yaml` (the app service only), `.dockerignore`; `smalls ingest` and `smalls tag`; `make ingest`, `make tag`, `make leak-test`, `make docker-test`. The container runs the tests and the image holds no `.env` and no key. `uv` on the host stays the primary path.
- **Store** (`smalls/tools/store.py`). In-process SQLite, in memory, rebuilt at every start (D3 stands): the ruled DDL, a `clock` of zero or one row, and the `visible`, `visible_point` and `visible_chunk` views. An unset clock shows nothing. The loader reads only paths under `integration.yaml` `sources:` and refuses `_internal/`, `scenario/` and `triggers/`, links included. Builds in about 215 ms.
- **Adapters** (`smalls/tools/ingest/`), one per source, all 17 `sources:` keys. **Parity: 5,861 of 5,861 manifest rows** match one store row on handle, path, locator, event time, known time and validity window, with no extra row (4,454 records and 1,407 metric points); `native` is the exact served bytes; zero unparsed lines.
- **Visibility.** 2,960 rows visible at 14:40 and 4,006 at 14:52, equal to the manifest, one version at most per record. The store half of the leak test passes at both (`make leak-test`). Fields whose served value is not true at `now` are withheld: named in `flags.withheld`, absent from `attrs`, left out by `store.render_native`.
- **Templates and sampling.** Every log record has a `template_id` (100 templates) and a `keep_1_in` and unit from the served `sampling:` block. Two ingests give the same dump hash.
- **Tagging** (`smalls/tools/ingest/tagging/`). Three facets from five profiles, 325 units asked 490 questions, a committed cache (`tags/cache.jsonl`) and a hand-labelled sample of 134 (`eval/tags/labels.yaml`, each label committed before the answer it is scored against). The taxonomy was run once as first ruled and amended the same day (decision record section 4.7): `act` is asked as one yes/no question per option, `signal` is asked both pick-one and as yes/no and also covers metric series, and the floor rule has a tolerance of 0.02. The floor is 0.90, measured: at it 110 of 113 stored values on the sample match the label (`signal` 62 of 62, `act` 36 of 39, `change_kind` 12 of 12) and none fails to reproduce over three repeats; 240 of 325 units are tagged. Tags are on by default since ruling G6 (`docs/decisions/tools-agent-build.md`) and load with zero cache misses; `SMALLS_TAGS=off` turns them off. Lint invariant 16 checks the profiles for bundle strings. `smalls tag --experiment` tries candidate facets without touching what ships.
- `make data` is unchanged, `make lint` 0 errors, `make test` 108 passed, all offline; the same tests pass in the container.

## Rulings and open points: step 6a
Recorded in `docs/decisions/ingestion-decisions.md`. Every point is ruled except O4, which is deferred.
- **O1, ruled: no "what the change touches" facet.** An extra set of yes/no labels on each change and commit saying what the change touches (calls to a dependency, resource limits, library versions, observability, feature flags, schema), so the agent could ask for changes by what they do. Left out, because the list reads as chosen with this incident's answer in mind, and `list_changes` already returns the whole day in one capped call.
- **O2, replaced by G6: tags are on by default, for now.** O2 ruled the default off until the ablation numbers were in. G6 (`docs/decisions/tools-agent-build.md`) turned it on ahead of them, so the agent is developed with the surface it will ship with. `SMALLS_TAGS` defaults to `on`; `off` turns them off. The ablation still runs both arms and the write-up reports the numbers; turning the default off again would be a ruling made on those numbers.
- **O3, ruled: the pinned classifier version stays in config.** The rule we had written as "no model identifiers" means model-agnostic design: the agent model and the classifier version are config values, and code, prompts and docs refer to them by role and assume no particular model. The pinned version in `smalls/tools/ingest/tagging/config.toml` is that rule applied, so nothing is open.
- **O4, deferred, and needs an answer before a held-out run: tags on held-out bundles.** We settle how held-out bundles are tagged separately, outside this step. Nothing waited on it while tags were off by default. With the default on (G6), a held-out run at the default would find no cached tags and every record would read as untagged, so either the held-out authoring session runs the tagging pass on its bundle, or held-out runs set the flag off and the write-up says so (open point P8 in `docs/decisions/tools-agent-build.md`).
- **O5, ruled: taxonomy v1 is three facets, not five.** `signal` (latency, errors, saturation, availability, traffic, change, security, routine, other) on log templates, alert definitions and titles, and incidents; `act` (observation, hypothesis, action_taken, action_proposed, question, status, social, other) on human chat messages, ServiceNow journal entries and PagerDuty notes; `change_kind` (release, rollback, config, infra, network, data, job, other) on change requests, Jenkins builds and commits. `layer` and `resolution` are left out: source, sourcetype and host are already columns, and the agent reads the 11 close notes in one call. `layer` is the first facet to add back if the ablation shows the agent failing to cross layers. A tag is applied only at high confidence: the chosen option's probability must reach a floor that starts at 0.8 and is set for precision (at least 9 in 10 applied tags matching the hand label), and anything else stores nothing. The profiles were read before the first `make tag` and run as shown; a change bumps the profile version. Amended after that run: `act` is asked as yes/no per option, `signal` is asked both pick-one and as yes/no and also covers metric series (both tags are lists), and the measured floor is 0.90 with a tolerance of 0.02 (`docs/DATA.md` 119 to 124). The hand-labelled sample is 134 units.
- **O6, ruled: of the other candidates, `signal` as yes/no is adopted and the rest are left out.** It was adopted on a test set in advance (at least 9 in 10 stored values matching on a larger sample labelled first; it met 37 of 37) and tags the lines that are two things at once, which pick-one left untagged. `resource` and `fault` are left out (precise and sparse, and their busiest values are this incident's own mechanism), and so is `layer` (it mostly restates the source). Numbers in `docs/research/tagging.md` section 7 and `docs/DATA.md` 124.

## Tools and agent: built
Built row by row from section 8 of `docs/decisions/tools-agent-build.md`, on branch `agent-build`. That record holds every row's acceptance evidence, and section 13 holds every judgment call. `make test` gives 582 passed and 1 skipped on the host, and the suite passes in the container.
- **Contracts.** The result envelope (`rows[]` with `t_known`, `referenced[]`, `meta`), the report schema with `subject`, `source`, `alerts[]`, `do_not`, `proposed_actions`, the structured `stakeholder_update` and the code-written `validation`, and `handle_class()`.
- **The eleven tools** (`smalls/tools/`), each reading only the store's views, with the source inventory, the values derived at `now`, the error sentences and the tag inputs. `smalls tools call|list|schema|smoke`. The tool-output half of the leak test calls all eleven at both checkpoints, with tags off and on, and asserts that every visible record can be reached.
- **The agent** (`smalls/agent/`): a LangGraph state graph with the opening sweep, the gates, the board, the validator, the verifier with one revision, carry-forward between checkpoints, the restart from the board, failure handling and the run record. `smalls run|resume|replay|show`.
- **The five blind files** (the four prompts and `smalls/tools/descriptions.yaml`) were written in a blind room by a fresh sub-agent and copied in unedited. The first author's fourteen questions are in section 11.1 of the record; we ruled on the five that touched open work (section 11.2: a role is what a hypothesis claims and never changes, a count of nothing has a citable handle, the action kinds' texts reach the model, and a check guards the contrarian prompt's tool list). The role ruling changed the prose, so the files in place are from a second blind pass (row C2b), whose ten questions are in section 11.3.
- **Evaluation harness** (`eval/`): the matrix runner with arms A and C and the tags flag as a dimension, process metrics in `summary.json`, the retrieval check (`docs/DATA.md` 127) and the scripted solve path (16 of 16 steps hold).
- **Commands.** `make test`, `make lint`, `make leak-test`, `make tools-smoke`, `make retrieval`, `make solve`; and, live, `make run`, `make resume RUN=<id>`, `make regress`, `make matrix`. Each also runs as `docker compose run --rm smalls <the same command>`.

## Next
Everything that can be built without a live model or a fresh session is built. What is next needs one or the other.

**The eval loop** (`docs/decisions/eval-loop.md`, planned 2026-10-14) is the next stage after the UI step. The scorer session (rows S1 to S7) starts now, alongside the UI. Once the UI and the scorer are both on `main`, the loop session runs the second provider's profiles and shakedown, a scored baseline, and then passes through the iteration room (V1) until the stop rule; then the retest on the current provider and the freeze. Its rulings V1 to V4 (V4: one model on the second provider, with effort varied by role) and its proposed rulings Q1 to Q7 are in sections 2 and 10.

Done: **the first live run** (row C3). Run `20261014T090933Z-5d5522` completed at the second checkpoint in 213 s and 19 of 30 calls, for $1.06; the verifier passed it, and its one proposed action stands deferred, as an unattended run's does. `make demo` replays it with no key, on the host and in the container. Strict tool schemas are off for the investigator: the provider refuses strict mode past 24 optional inputs, and ours have 38 (fact 4 of section 7.5 in the record). Section 13 of the record lists what the run showed that is left for the shakedown.

Waiting for a go-ahead, because it spends money:
- **The live shakedown** (row C4): the list in section 7.5 of the record; set the budgets from what is measured and write the numbers into section 6 of `docs/research/agent-design.md`.
- **The live carry-forward** (row E4) and **the tags ablation** (row E5): `make regress` with tags on and off.

Waiting for a fresh session:
- **Gate B** (row D5): a session with only the CLI and none of this track's context tries to find the cause. `make solve` shows the scripted path a person can follow; it is not that test.
- **The scorer and the answer key schema** (PLAN 3.3 and 5): owned by a session that does not write the agent. The verifier's keep-or-kill rule (row E3) waits for it.

Waiting for a ruling:
- **The second blind author's questions** (section 11.3 of the record). Each choice agrees with the code; 3, 4, 6 and 7 (confidence above 0.8, whether the leading hypothesis must be a `cause`, "unknown" in an impact field, how `subject` is named) are for the scorer session to confirm. A ruling that changes a blind file means a new blind pass, not an edit.
- **The cross-provider verifier profile.** Its client is an optional dependency group, `verifier-openai`, that the image does not install. One line in the Dockerfile's sync command adds it; this track did not edit that file.
- **O4 and P8**: how held-out bundles get tags, before any held-out run.

Before a live run, know that: the keys are read from `.env` by `uv run --env-file` and by compose, and never printed; a run record stores the names of key variables, never values; under pytest every profile is the scripted one unless `SMALLS_LIVE=1`; and `python -m eval.matrix --dry-run` prints what a matrix would start without starting it.

Open, not blocking (re-review findings, minor):
- Same-cart resubmits can be concurrent (29 of 102 gaps under 1 s), because a failed cart joins the resubmit pool when its request arrives, not when it fails; cart ids reuse the 20,000-id range about four times (`smalls_data/gen/model.py`).
- Gateway connections never rotate on `maxLifetime` (order-service ones do), so backends from the 10:20 restart are still live at 14:56.
- Connection lines land on exact .000 ms and some hosts add connections in the same millisecond; same family as the jitter we did not adopt.
- Before 14:28 only the anchor shows a 504 on a 2.14.2 pod in the logs at 1 in 100; the deploy-trap discriminator rests on that line, Datadog errors by version and errors surviving the rollback. Check at Gate B.
- The Datadog checkout error curve runs about 1.6 times the stream's gateway failures from 14:31 to 14:40, so log estimates under-read Datadog there.
- Older, not from the fixes: P-26101362's refusal evidence starts 2 min 42 s before the first 53300; "PSP slow calls" could not have paged in July under a 3 s timeout; webhook rows are the oldest match, where Splunk returns newest first (DATA.md 64).

Ruled since (G3 in `docs/decisions/tools-agent-build.md`), agent half done: each hypothesis has a `subject`, and the report lists `alerts: [{handle, verdict}]` with one verdict per alert stream the opening sweep showed. Still open on the scorer and data side: matching traps on `subject`, and the manifest field the alert verdicts are scored from.

## Rules that still apply
Raw files stay messy and vendor-native; everything is generated from `scenario/*.yaml`; never hand-edit `bundles/`; stop and report if the plan contradicts itself; do not read the held-out paths listed in CLAUDE.md; commit small and push often; keep the design model-agnostic: the agent model and the classifier version are config values, and code, prompts and docs refer to them by role.
