# Handoff: where the data phase stands

Updated 2026-10-13, after the step 7 and step 9 data and the agent design.

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
- Next agent step: port the spike into `smalls/agent/` (build row 1 of `docs/research/agent-design.md` section 5) once the step 6a tools exist, starting from `search_logs`. Live latency and cost numbers come from the local session's shakedown (build row 2); until then they stay "not measured". The only open agent question is the API key (agent-design.md section 7, row 1).
- The agent, its prompts, `smalls/tools/` and the scorer fall under the held-out rule in CLAUDE.md; a session that has read answer material must not edit them or `spikes/agent/`.

## Next
Step 6a of `docs/PLAN.md` section 6: the store and loader first. A session that writes `smalls/tools/` must follow the held-out rule in CLAUDE.md. Carry these from the re-review into 6a:
- The leak test needs a derived-state assertion as well as the timestamp and token scan: Datadog `overall_state` and PagerDuty service `status` (served as configured at 13:45), PagerDuty incident and ServiceNow incident versions (pick the version valid at `now`), Slack `reply_count`/`reply_users`/`latest_reply` and channel `members` (15:30 export values), the existence of a Dynatrace `.v2.json` or finished Jenkins build file before its `valid_from`, and metric response envelopes (`to_date`, `series[].end`) all have to be derived or clamped at `now`.
- Tools use `integration.yaml` `sampling:` only for the "sampled 1:N, estimated X" label; never print its class names or patterns to the agent (it names line classes that first appear after 14:52).

Open, not blocking (re-review findings, minor):
- Same-cart resubmits can be concurrent (29 of 102 gaps under 1 s), because a failed cart joins the resubmit pool when its request arrives, not when it fails; cart ids reuse the 20,000-id range about four times (`smalls_data/gen/model.py`).
- Gateway connections never rotate on `maxLifetime` (order-service ones do), so backends from the 10:20 restart are still live at 14:56.
- Connection lines land on exact .000 ms and some hosts add connections in the same millisecond; same family as the jitter we did not adopt.
- Before 14:28 only the anchor shows a 504 on a 2.14.2 pod in the logs at 1 in 100; the deploy-trap discriminator rests on that line, Datadog errors by version and errors surviving the rollback. Check at Gate B.
- The Datadog checkout error curve runs about 1.6 times the stream's gateway failures from 14:31 to 14:40, so log estimates under-read Datadog there.
- Older, not from the fixes: P-26101362's refusal evidence starts 2 min 42 s before the first 53300; "PSP slow calls" could not have paged in July under a 3 s timeout; webhook rows are the oldest match, where Splunk returns newest first (DATA.md 64).

Open from the final-pass plan review (not applied; to be ruled on before the report schema and scorer are built):
- a `subject` field per hypothesis (the one entity it blames; `entities` lists everything it touches), with traps matched on `subject`, and `change_any` restored on the checkout trap;
- an alert-membership check: the report lists `alerts: [{handle, verdict: this_incident | other_incident | noise}]`, scored from a manifest field `incident: this | other | none`.

Step 7 code still open: report schema (3.1, now with `do_not`, `proposed_actions` and the structured `stakeholder_update`), answer key schema (3.3), `itsm_query`, `get_oncall`, `get_topology`, the retrieval eval, the scorer and baseline. The data for them is in the bundle.

## Rules that still apply
Raw files stay messy and vendor-native; everything is generated from `scenario/*.yaml`; never hand-edit `bundles/`; stop and report if the plan contradicts itself; do not read the held-out paths listed in CLAUDE.md; commit small and push often; no model identifiers in commits, code or docs.
