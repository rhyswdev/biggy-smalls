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

**Spend in the first phase** (2026-10-14, a $10 budget, all on the second provider; `cost_usd` from each run's `summary.json`, the reviews' printed cost). In all: $8.13 over 22 runs, nine overfit reviews and a smoke test, leaving $1.87.

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

**Passes**, one line each. Lines changed are counted by `blind-install`. The guard set was not run in this first, $10 phase: no pass reached a keep that needed it, and the scorer does not exist yet, so "right" was judged by reading each report against PLAN 1.1 and 1.5.

| Pass | Finding | Lines changed | Before | After | Result | Cost |
|---|---|---|---|---|---|---|
| 1 | F-01, board updates in turns of their own | investigator 10, first_turn 2, descriptions 10 | 4 to 6 board-only turns in each of 5 runs | 5 in each of 3 runs | Reverted: the target did not move. Conclusions, calls and cost were unchanged | $1.13 |
| 2 | F-02, claims say more than their rows | investigator 6, descriptions 15 | 76 of 129 claims partial (59%) over 5 runs; 14 to 19 disputed a run | 26 of 80 partial (32%) over 3 runs; 10 disputed in each | Kept. Same conclusions in 3 of 3, about 27 claims a run as before; median cost $0.42 against $0.35 (inside the baseline's spread), wall 177 to 194 s against 219 to 262 s | $1.21 |
| 3 | F-03, an open hypothesis with no support and nothing said | investigator 2, descriptions 4 | left open with no open question in 5 of 10 runs | in 0 of 2 runs that had such a rival: each named it in an open question with the check that would decide it | Kept. Same conclusions in 3 of 3; median cost $0.35; partial claims 35 of 84 (42%), against 32% after pass 2, on three runs each and with no change to the citation rules, so read as noise until more runs say otherwise | $1.12 |

**Where the first phase ended.** The installed files are pass 3's (pass 2 and pass 3 kept, pass 1 reverted), on branch `eval-loop`, not yet merged into `main`. On those files, over five runs (three at 14:52 and one at 14:40 from `p03`, two at 14:52 from `final`): every run led with the right cause, read against PLAN 1.1, at 0.8. Every run rejected the decoys the trigger names, took the owner from on-call, and listed the harmful steps in `do_not`. Every run that kept a weak rival open named the check that would decide it. 59 of 145 claims were partial (41%). Over all eight runs on files with pass 2 in place it was 85 of 225 (38%), against 59% before, so pass 2's gain is real but smaller than its first three runs showed. Each run cost $0.34 to $0.42, took 195 to 229 s and 11 to 12 model turns, and used 28 to 30 calls. Neither wrong-cause trigger steered the agent. Not done in this phase: the scorer (rows S1 to S7, which grade "right" in place of reading); `eval.bench` and the benchmark files (L1's second half); the drafter reading its role from the set (the API still names its own profile); the Claude retest (L7); and a change outside the prompt for F-01.

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
