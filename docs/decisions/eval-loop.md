# Build plan: the eval loop and prompt iteration

Ruling record and build plan, written on 2026-10-14 while the UI step (`docs/decisions/ui-build.md`, branch `ui-build`) was at row R14. It covers what happens after the UI merges: making runs scoreable, measuring a baseline, and then a loop driven by the evals that improves the five blind files and the agent's efficiency, ending in the freeze tag `agent-v1` that the held-out run needs. It assumes ruling U15 of the UI step (the overfit review), which reaches `main` with the UI.

A fresh session can work from this file plus PLAN sections 5, 6.1, 12 and 13, `docs/decisions/tools-agent-build.md` sections 3, 7.5, 8 and 11, and ruling U15. This file contains no incident content and no held-out content.

## 1. Where we are

- **Designed:** the eight eval layers of PLAN 6.1: tool layer, retrieval, outcome, grounding, process, robustness, approaches, generalisation. Also the regression set (arm C, second checkpoint, three runs) on every prompt or tool change, and tables generated from `eval/runs/`.
- **Built:** run records, the matrix runner (arms A and C, the tags flag, `--trigger`, `--profile`, `--reps`), process metrics in `summary.json`, the retrieval check and the scripted solve path. On `ui-build` there is also the overfit review (`make overfit-review`), and a UI that opens committed runs as recorded chats.
- **Not built on any branch:** the scorer, the answer key schema, the three hand-written reports and the claim-support judge. `make score` is a stub. They belonged to a scorer session, which has not been started. Row E3 (the verifier's keep-or-kill rule) waits on them.
- **Live runs:** two, neither scored. Run `20261014T090933Z-5d5522` on `main` completed in 213 s, using 19 of 30 calls, for $1.06; it read 72% of its input from cache, and its verifier marked 15 of 26 claims partial. One run through the API on `ui-build` cost $0.99 and took 201 s. We know what a run costs, but not whether it is right.
- **The gap.** Every blind pass so far started from placeholders (`smalls/agent/blind.py`, `placeholder_files`). The room holds no current prompt and no findings. Section 3 of `tools-agent-build.md` says a change after v1 is driven by run records, and CLAUDE.md says every change is a new blind pass. Nothing carries what a run showed into the room, and a pass that rewrites all five files cannot be credited with any one fix. This plan closes that gap.

## 2. Rulings (2026-10-14)

| # | Point | Ruling | Why |
|---|---|---|---|
| V1 | Prompt changes after v1 | **The iteration room.** After v1, a blind pass starts from the installed five files and one finding (section 6), not from placeholders. The author makes the smallest change that fixes the finding in a way that holds on any incident, and leaves the rest unchanged. This amends G4 for passes after v1. Who writes the files, the folder-only rule, unedited install, the checks and the commit holding only the five files all stand, with the overfit review (U15) added to the checks. | A rewrite from scratch changes everything at once, so a score that moves cannot be credited to the fix, and a working rule can vanish between passes. The author stays blind to the incident: the room holds generic findings and aggregate numbers, never records, names or times. |
| V2 | Model provider during the loop | Development runs use the second provider's profiles (section 3). The loop ends with a retest of the frozen candidate on the current provider, and which provider ships as the default is decided on those numbers. | Asked for: cheaper per run (about 40% less at the first run's token mix, if the cache holds; section 3), and model-agnostic design is already a rule. Prompts tuned on one model may not carry over to another, so the retest is a gate, not a formality. |
| V3 | The scorer session | Starts now, alongside the UI step, working in `eval/`. The loop session never edits the scorer, the judge, the judge's prompt or the answer key. A scorer problem the loop finds is reported to the scorer session. | The scorer is on the critical path, and its files are separate from the UI's. The session that tunes the prompt must not also be able to move the grader. |
| V4 | Tiers on the second provider | One model: the provider's strong tier at its middle price, for every role. Roles differ by reasoning effort, not by model. Its flagship and cheapest tiers are not used. | Asked for: the flagship is more than this task needs. Effort is the lever agent-design axis 9 names to try before a cheaper model. One model also means one cache namespace and one set of provider facts to confirm. |
| V5 | Models by role, from config | Every model call belongs to a role, and a named set in config maps each role to a profile and an effort (section 3). A run records the map it resolved. A benchmark is a committed file of sets and overrides (row L9). This replaces the first draft's `SMALLS_PROVIDER` switch. | Asked for: once the prompt is settled, benchmarks change the model behind one agent at a time and measure the effect, with no code change. A recorded map lets every table group runs by what they ran on. The judge is not a role, so the grader never moves with what is graded. |

Rulings proposed for review are in section 10. The build uses each one's default until it is ruled.

## 3. Models by role, from config

Everything that picks a model is config (V5). Model names live only in `smalls/agent/config.toml` and the scorer's own config. Adding a model to compare later is a profile and its rates in that file; nothing else changes. Three pieces:

- **Profiles**, as now: one per model, with its provider, model, default effort, provider settings and rates under `[pricing]`.
- **Roles**: every agent that calls a model. `investigator`; `contrarian` and `claims`, the two verifier passes; `overfit`, the overfit reviewer (U15); `drafter`, the stakeholder drafter (U8). Arm A's single call is the `investigator`.
- **Sets**: a named map from every role to a profile, written `profile` or `profile@effort`.

```toml
default_set = "sol"

[sets.sol]                     # the loop's set (V2, V4)
investigator = "openai@medium" # until row L3 picks the loop's effort
contrarian   = "openai@medium"
claims       = "openai@low"
overfit      = "openai@medium"
drafter      = "anthropic-mid"

[sets.claude]                  # the current provider: row L7 and later benchmarks
investigator = "anthropic@medium"
contrarian   = "anthropic@medium"
claims       = "anthropic-small@medium"
overfit      = "anthropic@medium"
drafter      = "anthropic-mid"
```

**Resolution**, where the last one given wins: `default_set`, then `SMALLS_SET`, then `--set <name>`, then each `--role <role>=<profile[@effort]>` (or `SMALLS_ROLES`, a comma-separated list, for compose). `--effort <level>` is short for an override of the investigator's effort, and `--profile` stays as short for `--role investigator=<profile>`. A set must name every role. A set or override that names an unknown role, profile or effort is refused with a fixed sentence before any model is called. Each role's effort is fixed for the run, because changing it invalidates the cache.

**Recorded.** `run.json` holds the resolved map: for each role, its profile, provider, model, effort and rates. `summary.json` splits tokens and cost by role. Any table can then group runs by what they ran on.

**The judge is not a role.** It belongs to the scorer and is pinned in the scorer's own config, so a benchmark that changes the agent's models is always scored by the same judge. A run that brings in a second judge says so (row L7).

**The loop's set, `sol`.** One model (V4), which takes five efforts: low, medium (its default), high, xhigh and max. Per token, it costs about half the current investigator's input, output and cached-read rates.

| Role | Effort | Why |
|---|---|---|
| Investigator | medium, until row L3 picks the loop's effort | The role that decides the outcome |
| Contrarian pass | as the investigator | It needs the strongest reading. The design gave it the investigator's profile, and that stands |
| Claim-support pass | low | Short reading of packets |
| Overfit review | as the investigator | It reads diffs against the answer, a strong reader's job |
| Stakeholder drafter | `anthropic-mid`, unchanged (U8) | Not part of the investigation and not in the loop |
| Judge (scorer config) | high, pinned | A yes/no checklist with quotes, a few calls per run. It is the investigator's model, so a judge from the other provider family scores L7's comparison, and S4's hand labels measure its agreement |

The effort sweep (low, high and xhigh beside medium) and the ceiling probe (max, only if xhigh misses the outcome bar) are the first benchmark file, run in L3.

**What a run costs.** Planned at the first run's token mix, a run at medium was estimated at about $0.62. That estimate assumed cache writes were free, and they are not: corrected below. Measured on 2026-10-14 over eight runs at medium (section 9), a run costs $0.31 to $0.43, about $0.36 on average. It takes 11 to 14 model turns, uses 30 tool calls and 230 to 260 s, and reads 80 to 85% of its input from cache. This model writes far less output than the first provider's did: about 10k tokens a run against 29k.

The second provider caches by prefix on its own and reports what it writes. A write costs 1.25 times the input rate ($2.50 per million tokens), which the first draft of this section missed. Its rates above 272k input tokens per call are higher, and `max_input_tokens` keeps calls under them. Reasoning tokens bill as output.

The long-context rate applies above 272k input tokens per call. So `max_input_tokens` for each `openai` profile is set below that, and the restart guard (J16) fires before the rate doubles.

**Benchmarks.** A benchmark is a committed file, `eval/benchmarks/<name>.toml`, naming its cells (a set, optionally with role overrides), the arm, checkpoint, tags and repetitions, and its budget. It names profiles, never models. `python -m eval.bench <file> --dry-run` prints every run it would start and an estimate from the measured cost per profile and effort. Without `--dry-run`, it runs the cells through the matrix, scores each run, and prints one table grouped by cell: outcome, grounding, process, and cost per role, with spread. The file is committed before its runs, like the keep rule.

```toml
# eval/benchmarks/investigator.toml: the investigator's model changes, everything else stays
budget_usd = 20
arm = "C"
checkpoint = "1452"
reps = 5

[[cell]]
name = "the loop's set"
set = "sol"

[[cell]]
name = "current provider investigating, the loop's verifiers"
set = "sol"
roles = { investigator = "anthropic@medium", contrarian = "anthropic@medium" }
```

## 4. How it fits together

```text
now:        scorer session, rows S1..S7 ──────────────┐
            UI step, rows R14..R18 ───────────────┐   │
                                                  v   v
after both: L0 baseline on main ─> L1 roles and sets ─> L2 shakedown ─> L3 baseline
                                                                     │
            ┌────────────────────────────────────────────────────────┘
            v
            L4 failure analysis ─> L5 room ─> L6 one pass:
               finding ─> room ─> fresh author ─> install ─> 5 runs ─> score ─> keep or revert
            (repeat L6 until the stop rule, section 7)
            ─> L7 retest on the current provider ─> L8 freeze candidate, `agent-v1`
            ─> PLAN step 10 controls ─> held-out h1 (PLAN 12)
            ─> L9 benchmarks on the frozen prompt, each with its own go-ahead
```

Gate B (row D5 of `tools-agent-build.md`) still waits for a fresh session. Run it before L3: if it finds the cause unfindable, the bundle changes, and every run made before the change has to be run again.

## 5. Rows for the scorer session (start now)

Branch `scorer`, cut from `main`, in its own worktree. The held-out rule binds this session in full. It may read the answer key and the bundle. It edits `eval/`, plus the generator when the alert check needs a manifest field (a data change: `make data` and `make lint` pass, never a hand edit of `bundles/`). It edits no blind file and no agent code, apart from `handle_class()` for P4 under the overfit review. The `score` target joins the Makefile as a small hunk, because `ui-build` also edits that file.

| # | Build | Accepted when |
|---|---|---|
| S1 | The answer key schema (PLAN 3.3) over the existing `_internal/answer_key.yaml`, with rulings on the second blind author's questions 3, 4, 6 and 7 (`tools-agent-build.md` 11.3) and on P4 (are journal entries and paging notes classed with chat). A ruling that changes a blind file is noted as a finding for the loop, not edited. | The key validates. Each ruling is written into this record with its reason |
| S2 | `eval/scorer.py`, the deterministic part of PLAN 5: citation integrity (fabricated, unseen, future); cause and trigger matched 0, 0.5 or 1 through the alias table; fact recall at `as_of`; the trap table, matched on `subject` (G3); the owner check; the confidence check; the alert verdicts | A unit test per check; a report with a fabricated or future citation scores zero on integrity |
| S3 | Process measures that need the key: model turns, tool calls and tokens until the first correct leading hypothesis, read from the run's `hypothesis_update` events | Computed for any run record, and absent, not zero, when no correct leading hypothesis ever appears |
| S4 | The judge: one call per report, a yes/no checklist with quotes required, on its own system prompt in `eval/` (PLAN 5), and claim support (layer 4b). Its profile is read from the scorer's own config, so it never moves when the agent's provider does: the second provider's model at high effort (section 3). About 20 claims are labelled by hand and committed before the judge is scored against them, and the agreement is reported | Agreement is reported as counts. Offline, the judge is scripted. Live judge calls spend up to $5 without asking, with a running total in section 9 |
| S5 | Validation: three hand-written reports in `eval/reports/` (the correct one and the two wrong ones PLAN 5 lists) and arm A's report. The two existing live runs are scored | The scorer separates the three reports; the no-tools baseline falls for at least two traps; both live runs have `scores.json` |
| S6 | `make score RUN=<id>` writes `scores.json` into the run's folder, and `python -m eval.table <runs...>` prints the mean and spread of each measure over a set of runs, as counts (PLAN 12's wording rules). The matrix scores each run when the scorer is present | One command scores the regression set and prints its table |
| S7 | A summary safe for the room: aggregate counts per layer, with no trap names, no handles, no subjects and no times. This is what the room's `METRICS.md` holds | A test feeds every incident string to it and none comes out (the bundle-string lint over its output) |

When S5 passes, the session merges `scorer` into `main` (merging `main` in first if it has moved) and pushes.

## 6. Rows after the UI merges (the loop session)

Starts when `main` holds the UI (row R18) and the scorer (row S5). Branch `eval-loop`, cut from `main`, pushed after each row and merged into `main` at L8. L9 runs on `main` after that. The held-out rule binds this session in full. It may read run transcripts and scores. It never writes or edits the five blind files, and never edits the scorer, the judge or the answer key. Every agent-side change passes `make overfit-review`.

| # | Build | Accepted when |
|---|---|---|
| L0 | Baseline on `main` with the UI and the scorer merged | `make test` and `make lint` pass. The numbers are recorded here |
| L1 | The second provider's profiles: its client joins the default install, and its group is named on the Dockerfile's sync line; roles and sets as section 3 describes them: the profiles with their rates; the `sol` and `claude` sets; `SMALLS_SET`, `--set`, `--role`, `SMALLS_ROLES`, `--effort` and `--profile`, resolved in that order; the resolved map in `run.json` and cost per role in `summary.json`; the matrix taking sets and role overrides as dimensions; `eval/bench.py` with `--dry-run`; the overfit reviewer and the drafter reading their roles; the API starting runs on the default set; `max_input_tokens` under the long-context threshold; a stable prompt-cache key; its usage mapped to our fields (cached input as `cache_read`, no cache writes); reasoning items replayed unchanged, carried in the history rather than stored by the provider; strict tool schemas on if its strict mode accepts our surface, checked against the schema | A scripted run on each set records every role resolved; an override wins over the set, and the set wins over the default; a set that misses a role, or names an unknown role, profile or effort, is refused before any call; `eval.bench --dry-run` prints a scripted benchmark's runs and estimate; the model-name lint also scans `eval/benchmarks/`; the scripted suite passes; the overfit review passes; the image holds no key |
| L2 | The shakedown on the second provider: the eleven facts of `tools-agent-build.md` 7.5, restated for this provider, from one or two live runs at the second checkpoint. Budgets re-set from what is measured | Each fact is confirmed or answered. Cost per run, cache share and output per run are recorded in agent-design section 6. About $2 |
| L3 | The baseline. Arm C at the second checkpoint, medium effort: 5 runs. The effort sweep at the second checkpoint, as the first benchmark file (`eval/benchmarks/effort.toml`): low, high and xhigh, 3 runs each; max, 3 runs, only if xhigh misses the outcome bar. The first checkpoint at medium: 3 runs with carry-forward to the second (this is row E4, live). Arm A: 3 runs. The first live run on the current provider, scored as the reference. Row E3, the verifier's keep-or-kill rule, evaluated on these runs | A table generated from the run records covers outcome, grounding, process and cost per effort, with spread. The loop's effort is the lowest whose runs reach the cause with non-chat support as often as the best level's, and is written here. The keep-rule thresholds are fixed from the measured spread at that effort before any change (section 7). About $13 |
| L4 | The first failure analysis. Every baseline run is read in the UI as a recorded chat, or with `smalls show`, noting the first thing that went wrong in each. The notes are grouped into failure modes, each counted across runs, then ranked: outcome first, then grounding, then efficiency. Each mode is classed as prompt, tool or code, data, or model limit (the effort sweep, and the max probe when it runs, decide the last) | `eval/findings/ledger.yaml` holds each mode, with the runs and calls that show it (eval side, never in a room), and one generic finding text per prompt mode (section 8) |
| L5 | The room: `python -m smalls.agent blind-room <dir> --iterate <finding>` writes everything today's room holds, but with the five installed files in place of placeholders and no `prompt-v0.txt`. It adds `FINDING.md` (one finding), `HISTORY.md` (each earlier pass: its finding, the author's `CHANGE.md`, kept or reverted, aggregate numbers), `METRICS.md` (row S7's output) and a brief for iteration. The findings lint refuses a finding that carries a bundle string, a handle, a clock time or a run id, and the overfit reviewer reads the finding before it enters a room. After the pass: `blind-check`, the bundle-string lint, the overfit review of the diff, and the diff's size printed | Tests: a room holds exactly its listed files; a planted finding with a handle, a time or a bundle string is refused; the brief's text is checked like today's |
| L6 | The loop. One pass is: one finding, a room, a fresh sub-agent given only the room's path, install unedited, the regression set (section 7), score, then the keep rule. A pass that is kept also runs the guard set, then commits with the finding's id, the motivating run ids and the result. A pass that is reverted is a `git revert` of its install commit, and the record keeps both. A code finding follows the same keep rule and the overfit review, in its own commit, and is never in the same pass as a prompt change | Each pass is a line in section 9's table: finding, change size, kept or reverted, its numbers, its cost |
| L7 | The retest on the current provider, as a benchmark file (`eval/benchmarks/retest.toml`): the frozen candidate's regression and guard sets on the `claude` set, at the loop's effort or its nearest named level, scored by the same judge. A second judge from the other provider family scores the comparison runs of both providers, and the two judges' agreement is reported | The default provider is ruled on these numbers and written here, with the cost per run of each |
| L8 | The freeze candidate: the tags ablation (row E5) on it, with tags off and on; E3 if L3 did not settle it; HANDOFF, agent-design section 6 and the write-up's table generated from `eval/runs/`; tag `agent-v1` | PLAN 13's agent bar holds in at least 4 of 5 runs. PLAN 12's gates for a held-out bundle are checked; O4 and P8 (held-out tags) must be settled before h1 |
| L9 | Benchmarks on the frozen prompt. Each is a file under `eval/benchmarks/`, committed before its runs, with its own budget and its own go-ahead; none falls inside section 9's cap. The first three proposed: the investigator's model and effort changed, with the verifiers fixed; the verifier passes' models changed, with the investigator fixed (this also gives E3's keep-or-kill per profile); and the sets crossed. Any further model is a profile added to `config.toml` | Each benchmark's table is generated from its runs and committed with it. The write-up says what each shows. The prompt was tuned on the loop's set, so another set's numbers show how the prompt carries over, not a contest between models, and they cover the development incident only |

## 7. The keep rule and the stop rule

Proposed here and fixed in row L3, before the first change, from the measured spread. The defaults below apply until then.

**Regression set:** arm C, the second checkpoint, 5 runs, at the effort L3 picked (PLAN 6.1 says 3 runs; see Q2). No pass changes the effort, so a change in score is never a change in effort. **Guard set**, run only for a pass that would be kept: each of PLAN step 10's two framing perturbations (an incident commander asserting a wrong cause), 2 runs each through `--trigger`, plus the first checkpoint with carry-forward, 2 runs.

Keep a pass when all of these hold:

1. No run has a fabricated or future citation, and no run is a hard fail (a wrong leading hypothesis above 0.6).
2. The number of runs that reach the cause with non-chat support is no lower than the incumbent's.
3. The targeted failure mode appears in at least two fewer runs than before, or in none.
4. The median tool calls and the median cost per run are no worse than the incumbent's by more than its spread, unless 2 improved.
5. In the guard set, neither framing steers the leading hypothesis to the asserted cause; the first checkpoint's runs emit valid reports; and the continuity check passes.

Stop when any of these holds: PLAN 13's agent bar holds in at least 4 of 5 runs and the last two kept passes left the outcome unchanged; two passes in a row are reverted; eight passes are done; or the budget in section 9 is reached.

**Efficiency, once correctness holds:** model turns and tool calls to the first correct leading hypothesis; total calls; repeated calls; tokens per cited handle; cost and wall time per run; validator retries and refused calls (the first run lost a turn to a `submit_report` argument of the wrong type); and the length of the system prompt and tool definitions, which every call sends. A finding may be about efficiency alone, and a pass may shorten the prompt.

## 8. What a finding looks like

A finding names a generic failure mode, not an event. It may say what the agent did in terms that hold on any incident, how often, what the method says, and what would show the mode fixed. It may not name an entity, service, person, version, host, threshold, time, handle or run, or quote tool output.

```text
F-03 · prompt · seen in 4 of 5 runs
What happened: a rival hypothesis was rejected without reading any record that contradicts it; the
rejection rested on the leading hypothesis looking stronger.
What the method says: a rejected rival names the rows that clear it (agent-design 2.0).
Fixed when: rejected rivals carry an `against` row in at least 4 of 5 runs.
Keep: the opening sweep and the order of reads, which work.
```

The ledger keeps the link from each finding to its runs and calls, on the eval side. Only the generic text goes into a room.

## 9. Budget and the record of passes

**Proposed cap for this plan's live calls: $65.** Benchmarks (L9) are outside it: each file carries its own budget and needs its own go-ahead. The parts:

| Item | Estimate |
|---|---|
| Shakedown | about $2 |
| Baseline, with the effort sweep | about $13 |
| Reverted pass | about $3 |
| Kept pass | about $6 |
| Eight passes | about $35 |
| Retest on the current provider | about $11 |
| Judge calls and overfit reviews | about $3 |

Each live call's cost comes from its own record, with a running total kept here, one line per call. Before a call that could take the total past the cap, the session stops and asks.

**Spend in the first phase** (2026-10-14, a $10 budget, all on the second provider; `cost_usd` from each run's `summary.json`, the reviews' printed cost). In all: $9.79 over 26 runs, twelve overfit reviews and a smoke test.

| Item | Runs or calls | Cost |
|---|---|---|
| Smoke test of the profile (two small calls) | 2 calls | $0.03 |
| Overfit reviews: L1, L5, the findings, pass 1, pass 2's inputs and install | 7 calls | $0.11 |
| First run (`sol-shake`) and baseline (`sol-base`) | 5 runs | $1.77 |
| Pass 1 (`p01`) | 3 runs | $1.13 |
| Low-effort probe (`eff-low`) | 2 runs | $0.64 |
| Pass 2 (`p02`) and its overfit review | 3 runs, 1 call | $1.21 |
| Low effort on the pass 2 files (`p02-low`) | 2 runs | $0.62 |
| F-03's two reviews, pass 3 (`p03`) and its review | 3 runs, 3 calls | $1.14 |
| The two wrong-cause triggers on the pass 3 files (`frame-*`) | 2 runs | $0.82 |
| Two more runs on the pass 3 files at 14:52 (`final`) | 2 runs | $0.69 |
| A factual-question trigger (`frame-db-at-time`), F-04's two reviews, pass 4 (`p04-*`) and its review | 4 runs, 3 calls | $1.66 |

**Passes**, one line each. Lines changed are counted by `blind-install`. The guard set was not run in this first, $10 phase: no pass reached a keep that needed it, and the scorer does not exist yet, so "right" was judged by reading each report against PLAN 1.1 and 1.5.

| Pass | Finding | Lines changed | Before | After | Result | Cost |
|---|---|---|---|---|---|---|
| 1 | F-01, board updates in turns of their own | investigator 10, first_turn 2, descriptions 10 | 4 to 6 board-only turns in each of 5 runs | 5 in each of 3 runs | Reverted: the target did not move. Conclusions, calls and cost were unchanged | $1.13 |
| 2 | F-02, claims say more than their rows | investigator 6, descriptions 15 | 76 of 129 claims partial (59%) over 5 runs; 14 to 19 disputed a run | 26 of 80 partial (32%) over 3 runs; 10 disputed in each | Kept. Same conclusions in 3 of 3, about 27 claims a run as before; median cost $0.42 against $0.35 (inside the baseline's spread), wall 177 to 194 s against 219 to 262 s | $1.21 |
| 3 | F-03, an open hypothesis with no support and nothing said | investigator 2, descriptions 4 | left open with no open question in 5 of 10 runs | in 0 of 2 runs that had such a rival: each named it in an open question with the check that would decide it | Kept. Same conclusions in 3 of 3; median cost $0.35; partial claims 35 of 84 (42%), against 32% after pass 2, on three runs each and with no change to the citation rules, so read as noise until more runs say otherwise | $1.12 |
| 4 | F-04, the commander's request argued against but not answered | first_turn 2, investigator 2, descriptions 6 | 0 of 2 runs answered a requested step or a stated cause as asked (a factual question was answered) | 3 of 3 answered as asked: a stated cause answered "no" with the reason first; a requested plan given in full beside the evidence that it would not help | Kept. Leading hypothesis unchanged at 0.8 in 3 of 3; stated causes still rejected with rows; cost $0.38 to $0.46 | $1.24 |
| 5 | F-05, the agent chooses its own first reads (harness and prompt, ruled) | first_turn and investigator | not measured on its own: it changed every run's first turn | P3 and P4 are the first runs on it, with pass 6 | Installed | - |
| 6 | F-06, a quiet board is nudged, not refused (harness and prompt, ruled in P1) | investigator 5 | 4 reads refused in round 5 of each of 2 hand runs | no refusal in 16 runs; 3 nudged runs each moved the board the round after | Installed with P1's harness change | $0.02 |
| 7 | F-07, the summary relies on a link the board does not hold | investigator 8, descriptions 4 | trigger on the board in 1 of 7 sol runs (P4) | in 5 of 5 (3 regression, 2 guard), each with non-chat support | Kept. Leading cause unchanged at 0.75 to 0.8 in 5 of 5; partial claims 49 of 95 (per run 0.50 to 0.53, inside P4's 0.30 to 0.56); median cost $0.41 against $0.36 (inside P4's spread); calls 30 as before; neither wrong-cause trigger steered it | $2.05 |
| 8 | F-09, the leading statement chains links its rows do not each show | investigator 6, descriptions 7 | leading statement partial in 5 of 5 (pass 7) | partial in 4 of 5; partial claims 52 of 151 against 49 of 95; an effect kept as an open cause in 3 of 5, against 0 of 5 | Reverted: the target moved by one run, short of two, and the rival handling regressed. Leading cause unchanged at 0.7 to 0.8 in 5 of 5; median cost $0.41 | $2.06 |

**Where the first phase ended.** Raised in review after pass 3: when redirected, the agent should check the request and answer it, not decline it; pass 4 does that. The installed files are pass 4's (passes 2, 3 and 4 kept, pass 1 reverted), on branch `eval-loop`, fast-forwarded into `main` at `3466da5` once `make test` (676 passed, 3 skipped) and `make lint` passed. On those files, over five runs (three at 14:52 and one at 14:40 from `p03`, two at 14:52 from `final`): every run led with the right cause, read against PLAN 1.1, at 0.8. Every run rejected the decoys the trigger names, took the owner from on-call, and listed the harmful steps in `do_not`. Every run that kept a weak rival open named the check that would decide it. 59 of 145 claims were partial (41%). Over all eight runs on files with pass 2 in place it was 85 of 225 (38%), against 59% before, so pass 2's gain is real but smaller than its first three runs showed. Each run cost $0.34 to $0.42, took 195 to 229 s and 11 to 12 model turns, and used 28 to 30 calls. Neither wrong-cause trigger steered the agent. Not done in this phase: the scorer (rows S1 to S7, which grade "right" in place of reading); `eval.bench` and the benchmark files (L1's second half); the drafter reading its role from the set (the API still names its own profile); the Claude retest (L7); and a change outside the prompt for F-01.

**The second pass's author left two questions** in its room, copied here as written in short. Neither was answered to it.
1. Which handles go into a hypothesis's claim-support packet? It assumed `support`, plus `against` for a rejected hypothesis. What the code does: the packet holds both `support` and `against` for every hypothesis (`smalls/agent/verifier.py`, `packets`).
2. Are the verdict counts in METRICS.md the claim-support pass's? Yes.

**Guard: the two wrong-cause triggers** (PLAN step 10, written into `eval/framing/`), one run each at 14:52 on the pass 3 files. Neither steered the leading hypothesis. Asked to confirm the database was the cause, the run recorded that claim as a stated hypothesis, rejected it with four rows against, and led with the gateway at 0.8. Its summary opens by saying the database is out of headroom but the evidence does not support a database fault, and it proposed rolling the gateway back. Asked for a plan to redo the checkout rollback, the run's summary opens "Do not redo checkout rollback", citing the build that deployed the older version, the scaled-down replica set and errors continuing afterwards; it also led with the gateway at 0.8. The verifier sent that report back once.

**Probes.** Effort, on the pass 2 files: one run at each checkpoint at low reached the right leading cause in 161 and 173 s for $0.30 and $0.32, but 21 of 42 claims came back partial (50%, against 32% at medium) and the reports were thinner; one left out a contributing hypothesis that every medium run had. Medium stays the loop's effort. Earlier, on the original files at 14:52: two runs at low reached the same conclusions as medium, with every rival rejected, in 142 and 150 s against 231 to 248 s, for $0.29 and $0.35. The loop stays at medium so passes compare like with like. The set's investigator effort is a config change to make at the end, on more runs.

## 10. Proposed rulings and open points

| # | Point | Default the build uses |
|---|---|---|
| Q1 | The keep rule and stop rule of section 7 | As written, with the thresholds fixed in L3 from the measured spread |
| Q2 | Regression set of 5 runs, not 3 | 5. With 3, the spread between runs is as large as most fixes; at the second provider's cost, 5 runs cost what 3 did |
| Q3 | The two framing perturbations move from PLAN step 10 into the guard set | Yes. A prompt that has learned this incident shows it when a wrong cause is asserted, and finding that out per pass is cheaper than finding it at held-out. The contrarian control and the alias shuffle stay in step 10 |
| Q4 | The judge on the investigator's model at high effort, pinned, with a judge from the other provider family for L7's comparison | As written. It is the same model as the investigator, so S4's hand-labelled agreement is reported before any score is relied on |
| Q5 | The tags ablation (E5) runs on the freeze candidate, not now | On the candidate: it measures what ships |
| Q6 | A pass may carry two findings that touch different files, if time is short | No. One finding per pass |
| Q7 | Gate B runs before the baseline | Yes, as the last section 4 note says |
| O4, P8 | How held-out bundles get tags | Still open; must be settled before h1 |

## 11. What this plan does not change

The author rules of G4 (every rule generic, no entity or vendor-specific heuristic, tools described by what they do); the held-out rule; U15; the eval layers and their order; PLAN 12's wording rules for the write-up (counts, never percentages, and never "accuracy", "generalises", "robust" or "validated"); and the rule that development evals are not held-out evidence.

## 12. Phase 2 (planned 2026-10-14)

Since the first phase: an end-to-end pass fixed eight bugs in the web app (`docs/decisions/ui-build.md` 12.4); a call whose arguments fail validation is answered before the gate and retried out of sight (E9); the agent chooses its own first reads, with no opening sweep (pass 5, F-05, and agent-design ruling 5 re-ruled); and a restart shows the alert streams the report still owes a verdict. Every run now does something different in its first turn, so the first phase's numbers are not a baseline. The second provider's account ran out of credit on 2026-10-14; it is topped up before P0.

### 12.1 Rulings (2026-10-14)

| # | Point | Ruling | Why |
|---|---|---|---|
| V6 | A second model on the second provider | Amends V4. The provider's cheaper tier, luna, is tried at high effort beside sol at medium. The loop's model is the cheaper of the two whose baseline runs reach the cause with non-chat support as often, with a partial-claim share inside the other's spread. Model ids and rates come from the provider's own model list and price page, into `config.toml` only. | Asked for: a cheap test of whether high effort on the cheaper tier does what the middle tier does, since every pass pays for a regression set. |
| V7 | The record of tuning over time | Every live run gets a row in a committed results ledger, `eval/results/runs.jsonl`, written from its run record by `python -m eval.results add`. `docs/tuning-log.md` is generated from it by `python -m eval.results report`: one table per batch, each tied to its pass and commit, with the measures across batches. Scores are added to the rows when the scorer lands. Run records stay local, in a git-ignored `eval/loop-runs/`, so the UI's recorded chats are not flooded. The first phase's 26 records survive only in the `eval-loop` worktree; they are copied in (read only from there) and backfilled, with the committed run and this session's four CLI runs. A page published from the log at the end of each session shows the evolution. | Asked for. The passes are recorded as prose (HISTORY.md), per-finding numbers (ledger.yaml) and section 9's table, but the runs behind them are not committed and nothing shows a measure moving across passes. The write-up should show how the agent evolved. |
| V8 | Order | Phase 2 runs before the scorer exists and is judged by reading, as the first phase was: the leading cause with non-chat support (PLAN 1.1 and 1.5), the verifier's partial and unsupported claim counts, and calls, turns, cost and wall time. The scorer session (section 5) may start now, in parallel, in its own worktree. When it lands it scores every ledger row, and phase 2's keep decisions are re-checked on the scores. | Asked for: models now, scoring next. Both at once loses nothing, because the ledger keeps every run scoreable. |
| V9 | Budget | $20 of live calls for phase 2; the judge's calls are the scorer's (S4, $5). A running total in 12.3, one line per batch. The session stops and asks before a call that would cross it. | Asked for. |

### 12.2 Rows

On `main`, pushed after each row. The held-out rule binds this session in full; it never writes the five blind files; every agent-side change passes `make overfit-review`.

| # | Build | Accepted when |
|---|---|---|
| P0 | Preconditions: the second provider's account has credit (one small call to each model); `make test`, `make lint` and `make web-test` pass on `main`. | Both models answer and the suites pass. With no credit, the session stops here and says so |
| P1 | **Stale-board refusals become a nudge, before any eval run.** Seen in two chats run by hand on 2026-10-14: the model read for three rounds before its first board write, or between writes, and rule J14 refused the fourth round's reads ("the board has not changed in 3 rounds"), costing a turn and showing the commander rows marked ✕. One quiet round was a round of malformed calls, which no longer counts (E9); the rule also counted rounds while the board was still empty, likelier now that there is no opening sweep. **Ruled (2026-10-14), amending J14:** the gate no longer refuses reads for a quiet board. When the board has not changed for `stale_rounds` rounds, empty or not, the round's status line says so with what to do (record what the evidence shows with `update_board`, or submit), and nothing reaches the commander's screen. Prompt wording alone was tried in pass 1 (F-01) and did not move when the model writes the board, so the harness carries the nudge; the prompt only has to match it, and its stale line still says the gate refuses reads, so finding F-06 goes through the iteration room. A test pins the behaviour; overfit review | No run has a board refusal; the scripted test shows the nudge on the status line; F-06 is installed; P3 and P4's ledger rows carry stale rounds per run and whether the board moved after the nudge. If boards stay quiet for long stretches, a later ruling may bring back a refusal after a longer spell, out of the commander's sight like E9's retries |
| P2 | The record (V7): `eval/results/`, `eval.results add` and `report`, `eval/loop-runs/` ignored, the backfill and `docs/tuning-log.md`. A row holds: run id, date, batch, pass, commit, prompt and tools hashes, the resolved role map (model and effort per role), arm, checkpoint, tags, trigger (standard or framing), status, wall time, model turns, tool calls, policy refusals, malformed retries, cost, cache share, the leading hypothesis (subject and confidence), rejected subjects, the verifier's verdict and claim counts, and `scores`, empty until the scorer | A test builds a row from a fixture run record. Every surviving live run has a row; the log renders a table per batch and a line per measure across batches |
| P3 | Luna (V6): its profile and rates, and a `luna` set (investigator and contrarian luna at high, claims luna at low; the overfit reviewer stays on sol at medium; the drafter unchanged), shown by `--dry-run`. Then the shakedown on the new opening: one run each of sol at medium and luna at high, at the second checkpoint | Each model's first turn holds its own reads; both reports validate; cost, cache share, refusals and retries are in the ledger. About $1 |
| P4 | The baseline: at the second checkpoint, 5 runs of sol at medium and 5 of luna at high; at the first, 2 of each. The loop's model is picked by V6 and written here | A table per batch in the log; the pick and its reason. About $5 |
| P5 | Failure analysis (L4) on the picked model's P4 runs: the first thing wrong in each, grouped into modes, counted, ranked (outcome, then grounding, then efficiency) and classed (prompt, tool or code, data, model limit) | The modes in `eval/findings/ledger.yaml`; one generic finding per prompt mode, through the findings lint and the overfit review |
| P6 | Passes (L6) until the stop rule of section 7 or the budget: one finding, the iteration room, a fresh author given only the room's path, install unedited, a regression set of 3 runs on the picked model (5 when the change sits inside the spread), section 7's keep rule judged by reading until scores exist, and for a pass that would be kept, the guard set (the two framing triggers, one run each). A code finding goes in its own commit under the overfit review | Each pass is a line in section 9's table and a batch in the log. About $12 |
| P7 | Close: 12.3's spend, section 9's pass table, HISTORY.md, the ledger, the log regenerated, the tuning-log page published, HANDOFF | The page shows each measure across every pass, the first phase's included |

### 12.3 Budget and spend

| Item | Estimate |
|---|---|
| P0, two small calls | about $0.05 |
| P1, scripted, and F-06's overfit reviews | about $0.20 |
| P3, the shakedown | about $1 |
| P4, the baseline | about $5 |
| P5, overfit reviews of the findings | about $0.30 |
| P6, about five passes | about $12 |
| Total | under $20 |

Spend in phase 2, one line per batch; cost from each run's `summary.json` and each review's printed cost.

| Batch | Calls or runs | Cost | Running total |
|---|---|---|---|
| P0: one small call to each model | 2 calls | $0.00 | $0.00 |
| P1: overfit reviews of the harness diff (twice), of F-06, and of the installed prompt | 4 calls | $0.06 | $0.06 |
| P3: overfit review of the luna profile and set; one shakedown run each of sol at medium ($0.41) and luna at high ($0.05) | 1 call, 2 runs | $0.48 | $0.54 |
| P4: the baseline, 7 runs on sol at medium ($2.43) and 7 on luna at high ($0.25) | 14 runs | $2.68 | $3.22 |
| P5: overfit reviews of F-07 to F-09 (three rounds, two rewrites) | 3 calls | $0.04 | $3.26 |
| P6 pass 7 (F-07): its overfit review, 3 regression runs ($1.27) and 2 guard runs ($0.77) | 1 call, 5 runs | $2.05 | $5.31 |
| P6 pass 8 (F-09): its overfit review, 5 regression runs ($2.03) and the revert's review | 2 calls, 5 runs | $2.06 | $7.37 |

### 12.4 What each row showed

**P0 (2026-10-14).** The provider's model list holds sol at the version the loop already uses, but luna only at the version before it, so luna's id is that one; both ids are in `config.toml` only. Its price page gives luna $0.10 input, $0.01 cached input, $0.125 cache writes and $0.50 output per million tokens, and higher above 272k input tokens per call ($0.20, $0.02, $0.25 and $0.75); sol's rates are unchanged. Luna's model page gives a 1,050,000-token window (922,000 input), efforts `none` to `max` with `medium` the default, the Responses API, function calling and prompt caching. One small call to each model through the agent's own factory answered, so the account has credit. `make test` and `make lint` passed (680 passed, 2 skipped; 0 errors). Two test faults were found and fixed: the port check's test listened with a backlog of one, so a probe could find the queue full and call the port free (it failed about half the time; 0 of 15 after); and the browser test of the chat's exec draft read the API before the server action that keeps the "Copied" mark had answered (it failed 3 of 3; 12 of 12 after, by polling). `make web-test` then passed in full. A running `make app` holds the web folder's dev-server lock, so the browser suite cannot run beside it.

**P1 (2026-10-14).** The gate no longer refuses reads for a quiet board (`smalls/agent/gate.py`; `board.stale_refusal` is replaced by `board.quiet_rounds` and `board.nudge`). After `stale_rounds` rounds with no board change, empty board or not, the status line carries "the board has not changed in <n> rounds: record what the evidence shows with update_board, or submit" between the stale ids and the budget, in every round until the board moves; a round of only malformed calls does not count, and a follow-up starts the count again. The `board_status` event carries the nudge and the quiet count apart from its `line`, which is what the terminal draws, and the web app draws no status line, so the commander sees nothing. `summary.json` records `process.quiet_board`: the nudged rounds and how many the board answered in the next round. A scripted test pins it (`tests/agent/test_method.py`: six reads over five quiet rounds all run, the nudge on rounds 3 to 5, none on the screen's line). Overfit review of the harness diff: 11 hunks, 0 flagged. The two hand-run records that showed the refusal (`20261014T173259Z-6b8ce8` at 14:52 and `20261014T173445Z-4fe561` at 14:40, both on the first provider) each had four reads refused in round 5. F-06 went through the iteration room: the finding passed the findings lint and the overfit review, the fresh author changed only `investigator.md` (5 lines: the status-line form, the stale rule without the refusal, and a rule for the nudge with `<n>` in place of a number), `blind-check` and `make lint` passed, and the installed diff passed the overfit review (1 hunk, 0 flagged). The author left no questions. Installed as pass 6 at `eeff055`. Whether any run is refused, and whether boards move after a nudge, is read from P3 and P4's ledger rows.

**P2 (2026-10-14).** `eval/results.py` builds a ledger row from a run record by code (`add`) and writes `docs/tuning-log.md` from the rows (`report`). A row holds the run id, date, batch, pass, phase, commit, prompt and tools hashes, the set, every role as model and effort and as profile and effort, arm, checkpoint, tags, the trigger (`standard`, `framing:<name>` or `custom`, found by comparing the run's trigger with its checkpoint's file and `eval/framing/`), status, wall time, model turns, tool calls, policy refusals, board refusals, malformed calls retried out of sight (from E9 on; empty before), the quiet-board nudges and moves (from P1 on), cost, cost by role from `usage.json`, cache share, the leading hypothesis (subject, confidence, role), the rejected and open subjects, the verifier's status and claim counts, `validation.ok`, and `scores`, empty until the scorer. What each batch was for is written in `eval/results/batches.yaml`, and `add` refuses a batch that is not described there. The log names profiles, not models, because the model-name lint scans `docs/`. Run records stay in the git-ignored `eval/loop-runs/`. Backfilled: the first phase's 26 records, copied from the `eval-loop` worktree, the committed run C3, and the two hand runs of 2026-10-14 that left records (the other two of the four left none). That is 29 rows in 13 batches; the first phase's runs sum to $9.60, which with its reviews and smoke calls is section 9's $9.79. Tests: `tests/agent/test_results.py` (a row from a scripted run record, `add` replacing a row and refusing an undescribed batch, the trigger kinds, and the log's tables and lines). One thing the backfill shows that the first phase's prose did not: one first-phase run (`p02-low`) also had a board refusal.

**P3 (2026-10-14).** Luna's profile (`openai-luna`, settings as sol's, strict tool schemas on), its rates and a `luna` set are in `config.toml`; the overfit review passed (3 hunks, 0 flagged). `eval.matrix --dry-run` now prints what each role resolves to, so the set was checked before any spend: investigator and contrarian on luna at high, claim support on luna at low, the overfit reviewer on sol at medium, the drafter unchanged. The shakedown, one run each at the second checkpoint on the pass 6 files (`p3-sol`, `p3-luna` in the log):
- Both completed with valid reports and led with the gateway (sol at 0.8, verifier pass; luna at 0.7, its subject written as the gateway's version and its retry path, sent back once by the verifier). Both rejected the host and the rolled-back service; luna also rejected the batch job, which sol kept open as contributing.
- Luna accepts our strict tool schemas: no call was refused for its schema.
- Luna's first turn held nine reads of its own choosing (alerts, changes, chat, three metric series, three log searches). Sol's first turn held only a board write, four hypotheses from the trigger, and its reads began in its second turn. So P3's "each model's first turn holds its own reads" holds for luna and not for sol: a turn spent on the board alone, the pattern F-01 measured, now at the very start.
- Sol: $0.41, 203 s, 13 turns, 30 calls, 79% of input from cache, no refusals, no malformed calls, no nudge. Luna: $0.05, 495 s, 9 turns, 34 calls (30 and a revision's 4), 77% from cache, one refusal (a read after the budget was spent), no malformed calls, no nudge. Luna spent its 30 calls in three turns.
- Luna is slow because it reasons at length: 23 to 38 s per turn against sol's 5 to 15, one report turn of 172 s and 15,392 reasoning tokens, against sol's longest of 59 s. At a 20th of sol's rates it still costs an eighth as much per run.
- Neither run was nudged, so neither shows yet whether a nudge moves the board.

**P4 (2026-10-14): the loop's model is sol at medium.** Seven runs on each model on the pass 6 files, five at the second checkpoint and two at the first (`p4-sol` and `p4-luna` in the log), run as five matrices side by side. Judged by reading each report against PLAN 1.1 and 1.5:

| | sol at medium | luna at high |
|---|---|---|
| Led with the cause (the gateway holding its transaction through the retried calls), with non-chat support | 7 of 7, at 0.6 to 0.8 | 2 of 7, at 0.8 |
| Led with something else | none | 4 with the database at 0.8 or 0.9 (the guilty-database trap, so a hard fail under section 7); 1 with the provider's slowdown, the trigger, at 0.6 |
| Partial claims | 75 of 179; per run 0.30 to 0.56, median 0.41 | 65 of 151; per run 0.22 to 0.67, median 0.41 |
| Cost per run | $0.26 to $0.39, median $0.36 | $0.03 to $0.04, median $0.03 |
| Wall time per run | 160 to 199 s, median 178 | 277 to 408 s, median 318 |
| Model turns, tool calls | 12 or 13; 30 | 7 to 10; 28 to 30 |
| Cache share | 0.80 to 0.85 | 0.72 to 0.82 |
| Board refusals, policy refusals, malformed calls | 0, 0, 0 | 0, 0, 0 |
| Quiet-board nudges | 1 run, round 10; the board moved the next round | 2 runs, rounds 6, and 5 and 6; each board moved the round after its last nudge |

By V6 the loop's model is the cheaper one only if it reaches the cause as often, and luna does not: it reached it in 2 of 7 against 7 of 7. So the loop stays on sol at medium, and the partial-claim test is moot. Luna's runs read widely in their first turn but take the database's exhaustion, which every source shows loudly, for the cause; the runs that got it right read the gateway's own logs and the design note. Whether luna does better on the prompt the loop produces is a benchmark for later (L9), not this phase.

P1's acceptance, from these 16 runs (P3 and P4): no run had a board refusal; three runs were nudged, and in each the board moved in the round after the last nudge. The quiet stretches were short (one or two rounds past the third), so nothing here argues for bringing a refusal back.

**P5 (2026-10-14).** Read: sol's seven P4 runs, with the P3 shakedown and the luna runs as context (`eval/findings/ledger.yaml`, `phase2_modes`). Every sol run led with the right cause, rejected the decoys the trigger names, took the owner from on-call and listed the harmful steps in `do_not`, so the first thing wrong in each was not the conclusion. The modes, ranked and classed:

| Rank | Mode | Runs | Class | Finding |
|---|---|---|---|---|
| 1 | The summary names what set the fault off, but no hypothesis holds it with the `trigger` role. Every standard run before pass 4 had one (17 of 17); since pass 4, 1 of 9 | 6 of 7 | prompt | F-07 |
| 2 | An effect of the leading cause kept as a separate open `cause` at 0.6; on luna the same confusion led (4 of 7) | 2 of 7 | prompt | F-08 |
| 3 | The leading statement chains several links and is judged partial; partial claims 75 of 179 overall, where pass 2 left them | 6 of 7 | prompt | F-09 |
| 4 | An open rival with no support of its own judged unsupported; pass 3 allows it with an open question, and each had one | 3 of 7 | prompt | none |
| 5 | Board updates in turns of their own, four a run, the first turn always among them; the right hypothesis leads at round 7 after 20 to 22 calls, against round 5 or 6 with the sweep | 7 of 7 | model limit (pass 1 tried the prompt) | none |

The overfit review flagged all three first drafts: F-07 and F-08 for carrying the incident's shape (F-08's as the guilty-database trap), F-09 for asking that the conclusions stay unchanged. The generic rewrites passed. P6 takes F-07 first, then F-08, then F-09.

**P6, pass 7 (F-07): kept.** The author changed `investigator.md` (8 lines) and the report-summary description (4 lines); the overfit review passed (4 hunks, 0 flagged). On three regression runs at the second checkpoint (`p07` in the log): every run led with the gateway at 0.8 with 8 to 13 non-chat rows, and every run recorded what set the fault off as a `trigger` hypothesis (0.6 to 0.8) with non-chat support, against 1 of 7 in P4. Keep rule: no hard fail and every report valid (1); the cause reached in 3 of 3, as in P4's 7 of 7 (2); the target mode in none of 3, against 6 of 7 (3); median calls 30 as before and median cost $0.41 against $0.36, inside P4's spread of $0.26 to $0.39 (4). Guard (`p07-guard`): asked to confirm the database, the report opens "No: the database is out of connections, but the evidence does not support a primary database fault" and leads with the gateway at 0.8; asked for a plan to redo the rollback, it gives the plan in full and then says the evidence does not support spending the ten minutes on it, leading with the gateway at 0.75 (5). Side effects: more hypotheses per report (a median of 9 in the regression runs and 7 in the guard runs, against 5 in P4), with the gateway's release as its own `contributing` link and the host the trigger blames as a `symptom`; the database rival was rejected in all five runs, so F-08's mode did not appear. The author noted one ambiguity for us to rule on: "trigger" names both the commander's message (the `{trigger}` slot) and the hypothesis role.

**P6, pass 8 (F-09): reverted.** F-08 was set aside first: on the pass 7 files no run kept an effect as an open cause (0 of 5), so a pass had no target, and a rule aimed at that mode sits close to a trap. Pass 8 took F-09. The author capped each hypothesis statement at one link, with the other links in hypotheses of their own, and changed `investigator.md` (6 lines) and two field descriptions (7 lines); the overfit review passed (4 hunks, 0 flagged). Three regression runs sat inside the spread, so two more were run (`p08` in the log). Over five: the leading cause was right in 5 of 5 at 0.7 to 0.8; the leading statement was judged `supported` in 1 of 5 (0 of 5 on pass 7), short of rule 3's two fewer; partial claims fell to 52 of 151 (34%, against 52% on pass 7 and 42% in P4); and in 3 of 5 runs an effect of the leading cause (connection exhaustion, as the gateway's or the database's) came back as a separate open `cause`, at 0.4 or 0.6, which no pass 7 run did. Reverted at `eb3da31`; the files are pass 7's again, byte for byte. The author noted that the claim-support pass defines `partial` as "the rest is inference or is not in them", so a statement that marks an unshown link as inference cannot be judged `supported`; whether it should be is a verifier question for us, not a prompt finding.

**P6 stops (2026-10-14).** Section 7's first stop condition holds: PLAN 13's agent bar, read against the runs, holds in 5 of 5 runs on the pass 7 files (valid reports, no fabricated or future citation, a leading hypothesis with non-chat support, an `against` row on every rejected one, the owner from on-call, a `do_not` list, a stop within the budget; carry-forward was not run in this phase), and the last two kept passes (4 and 7) left the outcome unchanged. One pass in a row was reverted, two of the stop rule's eight passes were used in this phase, and $12.63 of the $20 is left. The installed files are pass 7's.

**P7 (2026-10-14): closed.** Phase 2 spent $7.37 of its $20 (12.3). Section 9's pass table has passes 5 to 8; `eval/findings/HISTORY.md` has passes 6 to 8; the findings ledger has F-06 to F-09 and phase 2's ranked modes; the results ledger has 55 rows and `docs/tuning-log.md` is generated from it. Each row now also holds the report's hypotheses with their roles and the run's board-only turns, so the log follows the trigger on the board and F-01's mode across every batch; `python -m eval.results rebuild` refreshed every row and `export` wrote the page's data. The tuning-log page is published at https://claude.ai/artifact/Kqtg6nS1oDdbC5y4F4VvUQ: what each batch led with, the trigger on the board, partial claims, hypotheses, cost, wall time, model turns and board-only turns, for every batch from the first phase on, with the batch and run tables. `make test` (685 passed, 2 skipped) and `make lint` (0 errors) passed at the close. HANDOFF holds the summary and what is open for us.


## 13. Runs to score later (planned 2026-10-14)

While the scorer session builds S1 to S7, three sets of live runs can be made now on the pass 7 files and graded when the scorer lands; the ledger keeps every run scoreable. They are the parts of the freeze step (section 6, L3 and L8) that need no score to run: arm A for PLAN 13's "arm C beats arm A", row E4 (live carry-forward) and row E5 (the tags ablation). Nothing here changes a prompt, the agent or the tools, and nothing is kept or reverted: the runs are measurements of the files in place.

### 13.1 Rules

- Held-out rule in full; the five blind files are not written; the scorer's files (`eval/scorer*`, `eval/table*`, the judge and its prompt, the answer key, the Makefile's `score` target, `_internal/`) are not touched, because the scorer session owns them.
- The installed prompt files are pass 7's (`9e0e340`; pass 8 was reverted at `eb3da31`). R0 checks it; if they differ, stop and ask.
- Set `sol` throughout; the investigator at medium. Run records go to `eval/loop-runs/`; every run gets a ledger row in a batch described in `eval/results/batches.yaml` first.
- A run that fails for a reason in the code is reported, not fixed in this session, unless it blocks every run; a fix then follows U15 (overfit review) in its own commit.
- Budget: $7 of live calls, kept as a running total in 13.4, one line per batch. Stop and ask before a call that would cross it.

### 13.2 Rows

| # | Build | Accepted when |
|---|---|---|
| R0 | Preconditions: `git diff 9e0e340 -- smalls/agent/prompts smalls/tools/descriptions.yaml` is empty; `make test` and `make lint` pass; one small call to sol answers. Ledger rows gain `carried_from` (from `run.json`), with `rebuild` run once and the test extended | The diff is empty, the suites pass, every row has the field |
| R1 | Arm A, the no-tools baseline: `python -m eval.matrix --arms A --checkpoints 1452 --reps 3 --set sol --label armA --runs-dir eval/loop-runs` | Three valid reports in the ledger (batch `armA`); each run's leading subject and whether it fell for a trap in the commander's message (PLAN 1.5) recorded by reading in 13.3. About $0.30 |
| R2 | Carry-forward at the first checkpoint: three runs at 14:40 (`--checkpoints 1440 --reps 3 --label e4`) | Three completed runs in batch `e4-1440`. About $1 |
| R3 | Carry-forward to the second: one run at 14:52 per R2 run, each `python -m smalls.agent run --arm C --eval-run --durability exit --no-thinking --bundle <bundle> --now 14:52 --trigger-file <bundle>/triggers/1452.md --checkpoint 1452 --tags on --set sol --run-id e4-1452-from-r<n> --carry-from e4-C-1440-tags-r<n>` with `SMALLS_RUNS_DIR=eval/loop-runs` | Three runs in batch `e4-1452`, each with `carried_from` set; `validation.continuity` read; whether each cites handles dated between 14:40 and 14:52 (PLAN 13) recorded in 13.3, with what changed on the board between the two looks. About $1.30 |
| R4 | The tags ablation: five runs with tags off (`--tags off --reps 5 --label e5-off`) and two more with tags on (`--label e5-on --reps 2`), which with pass 7's three regression runs make five a side | Batches `e5-off` and `e5-on`; a table in 13.3 of the two sides: leading cause, trigger on the board, calls that used a tag input, tool calls, turns, partial claims, cost, wall time. No ruling on the default (G6): that waits for scores. About $2.80 |
| R5 | Close: the log regenerated; the page refreshed (export, upload the three files, update the three datasets, as HANDOFF says); readings added to `eval/results/notes.yaml` for anything the new batches change; 13.4's spend; HANDOFF | The page shows the new batches; HANDOFF says what each set showed and that all of it waits for the scorer |

### 13.3 What the runs showed

**R0 (2026-10-14).** The diff from `9e0e340` over the five blind files is empty, so the installed files are pass 7's. `make test` (685 passed, 3 skipped) and `make lint` (0 errors) pass; one small call to sol's investigator through the agent's own factory answered. Each ledger row now carries `carried_from` (the earlier run and its `as_of`, from `run.json`; null for a fresh look); `rebuild` refreshed all 55 rows and changed no other field; `tests/agent/test_results.py` covers both cases. The runs are made from a worktree whose `eval/loop-runs/` links to the main checkout's, so every record sits in the one local store.

### 13.4 Spend

| Batch | Runs | Cost | Running total |
|---|---|---|---|
| R0: one small call to sol | 1 call | $0.00 | $0.00 |
