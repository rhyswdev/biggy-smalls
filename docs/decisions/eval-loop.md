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

Rulings proposed for review are in section 10. The build uses each one's default until it is ruled.

## 3. Model profiles for the loop

Model names live only in `smalls/agent/config.toml` and the scorer's own config. Row L1 picks them from the provider's current list, by the tier each role below names, and records the rates beside them, like the existing entries.

| Role | Profile | Tier | Why |
|---|---|---|---|
| Investigator | `openai` | The provider's newest strong tier at its middle price | The role that decides the outcome. Per token, about half the current investigator's input, output and cached-read rates. Reasoning effort is fixed for the run, as now. |
| Claim-support pass | `openai-small` | Its cheapest current tier | Short reading of packets. The same rates as the current cheaper tier. |
| Contrarian pass | `openai` | As the investigator | It needs the strong model. The design chose the investigator's profile for it, and that stands. |
| Overfit review | the investigator's profile (U15) | As the investigator | It follows the active set. |
| Stakeholder drafter | `anthropic-mid` (U8) | Unchanged | It is not part of the investigation and is not in the loop. |
| Judge (scorer) | `openai-top`, in the scorer's own config | Its flagship tier | A yes/no checklist with quotes, a few calls per run, on the strongest reader available. It is a different model from the investigator, and it is pinned for the whole loop, so a change in score is never a change in judge. |
| Floor probe | `openai-small` as investigator | Cheapest | Three baseline runs, for a few cents. Shows whether the task needs the strong tier at all. |
| Ceiling probe | `openai-top` as investigator | Flagship | Three runs, only if the baseline misses the outcome bar, and only with a go-ahead (about $10). It tells a limit of the prompt from a limit of the model. |

One setting moves a whole set: `SMALLS_PROVIDER=openai|anthropic` selects the investigator, both verifier passes and the overfit reviewer together. `--profile` still overrides one run.

**What a run should cost.** These estimates are at the first run's token mix: about 147k input tokens sent for the first time, 368k read back from cache, and 29k output.

| Investigator set | Estimate per run |
|---|---|
| `openai`, with the cache holding as it did on the first run | about $0.62 |
| `openai`, with nothing cached | about $1.32 |
| `openai-top` | about $3.30 |
| `openai-small` | about $0.03 |

The second provider caches automatically by prefix and charges nothing to write. So the saving depends on its cache hit rate, which the shakedown (row L2) measures; without the cache, this provider costs more than the current one. Reasoning tokens bill as output, and this provider's output per run may differ from the first run's; L2 measures that too.

The long-context rate applies above 272k input tokens per call. So `max_input_tokens` for each `openai` profile is set below that, and the restart guard (J16) fires before the rate doubles.

## 4. How it fits together

```text
now:        scorer session, rows S1..S7 ──────────────┐
            UI step, rows R14..R18 ───────────────┐   │
                                                  v   v
after both: L0 baseline on main ─> L1 profiles ─> L2 shakedown ─> L3 baseline
                                                                     │
            ┌────────────────────────────────────────────────────────┘
            v
            L4 failure analysis ─> L5 room ─> L6 one pass:
               finding ─> room ─> fresh author ─> install ─> 5 runs ─> score ─> keep or revert
            (repeat L6 until the stop rule, section 7)
            ─> L7 retest on the current provider ─> L8 freeze candidate, `agent-v1`
            ─> PLAN step 10 controls ─> held-out h1 (PLAN 12)
```

Gate B (row D5 of `tools-agent-build.md`) still waits for a fresh session. Run it before L3: if it finds the cause unfindable, the bundle changes, and every run made before the change has to be run again.

## 5. Rows for the scorer session (start now)

Branch `scorer`, cut from `main`, in its own worktree. The held-out rule binds this session in full. It may read the answer key and the bundle. It edits `eval/`, plus the generator when the alert check needs a manifest field (a data change: `make data` and `make lint` pass, never a hand edit of `bundles/`). It edits no blind file and no agent code, apart from `handle_class()` for P4 under the overfit review. The `score` target joins the Makefile as a small hunk, because `ui-build` also edits that file.

| # | Build | Accepted when |
|---|---|---|
| S1 | The answer key schema (PLAN 3.3) over the existing `_internal/answer_key.yaml`, with rulings on the second blind author's questions 3, 4, 6 and 7 (`tools-agent-build.md` 11.3) and on P4 (are journal entries and paging notes classed with chat). A ruling that changes a blind file is noted as a finding for the loop, not edited. | The key validates. Each ruling is written into this record with its reason |
| S2 | `eval/scorer.py`, the deterministic part of PLAN 5: citation integrity (fabricated, unseen, future); cause and trigger matched 0, 0.5 or 1 through the alias table; fact recall at `as_of`; the trap table, matched on `subject` (G3); the owner check; the confidence check; the alert verdicts | A unit test per check; a report with a fabricated or future citation scores zero on integrity |
| S3 | Process measures that need the key: model turns, tool calls and tokens until the first correct leading hypothesis, read from the run's `hypothesis_update` events | Computed for any run record, and absent, not zero, when no correct leading hypothesis ever appears |
| S4 | The judge: one call per report, a yes/no checklist with quotes required, on its own system prompt in `eval/` (PLAN 5), and claim support (layer 4b). Its profile is read from the scorer's own config, so it never moves when the agent's provider does. About 20 claims are labelled by hand and committed before the judge is scored against them, and the agreement is reported | Agreement is reported as counts. Offline, the judge is scripted. Live judge calls spend up to $5 without asking, with a running total in section 9 |
| S5 | Validation: three hand-written reports in `eval/reports/` (the correct one and the two wrong ones PLAN 5 lists) and arm A's report. The two existing live runs are scored | The scorer separates the three reports; the no-tools baseline falls for at least two traps; both live runs have `scores.json` |
| S6 | `make score RUN=<id>` writes `scores.json` into the run's folder, and `python -m eval.table <runs...>` prints the mean and spread of each measure over a set of runs, as counts (PLAN 12's wording rules). The matrix scores each run when the scorer is present | One command scores the regression set and prints its table |
| S7 | A summary safe for the room: aggregate counts per layer, with no trap names, no handles, no subjects and no times. This is what the room's `METRICS.md` holds | A test feeds every incident string to it and none comes out (the bundle-string lint over its output) |

When S5 passes, the session merges `scorer` into `main` (merging `main` in first if it has moved) and pushes.

## 6. Rows after the UI merges (the loop session)

Starts when `main` holds the UI (row R18) and the scorer (row S5). Branch `eval-loop`, cut from `main`, pushed after each row and merged into `main` at L8. The held-out rule binds this session in full. It may read run transcripts and scores. It never writes or edits the five blind files, and never edits the scorer, the judge or the answer key. Every agent-side change passes `make overfit-review`.

| # | Build | Accepted when |
|---|---|---|
| L0 | Baseline on `main` with the UI and the scorer merged | `make test` and `make lint` pass. The numbers are recorded here |
| L1 | The second provider's profiles: its client joins the default install, and its group is named on the Dockerfile's sync line; the profiles of section 3 with their rates; `SMALLS_PROVIDER`; `max_input_tokens` under the long-context threshold; a stable prompt-cache key; its usage mapped to our fields (cached input as `cache_read`, no cache writes); reasoning items replayed unchanged, carried in the history rather than stored by the provider; strict tool schemas on if its strict mode accepts our surface, checked against the schema | The scripted suite passes with each provider set selected; the overfit review passes; the image holds no key |
| L2 | The shakedown on the second provider: the eleven facts of `tools-agent-build.md` 7.5, restated for this provider, from one or two live runs at the second checkpoint. Budgets re-set from what is measured | Each fact is confirmed or answered. Cost per run, cache share and output per run are recorded in agent-design section 6. About $2 |
| L3 | The baseline. Arm C at the second checkpoint: 5 runs. The first checkpoint: 3 runs with carry-forward to the second (this is row E4, live). Arm A: 3 runs. The floor probe: 3 runs. The first live run on the current provider, scored as the reference. Row E3, the verifier's keep-or-kill rule, evaluated on these runs | A table generated from the run records covers outcome, grounding, process and cost, with spread. The keep-rule thresholds are fixed from the measured spread before any change (section 7). About $7 |
| L4 | The first failure analysis. Every baseline run is read in the UI as a recorded chat, or with `smalls show`, noting the first thing that went wrong in each. The notes are grouped into failure modes, each counted across runs, then ranked: outcome first, then grounding, then efficiency. Each mode is classed as prompt, tool or code, data, or model limit (the ceiling probe decides the last, with a go-ahead) | `eval/findings/ledger.yaml` holds each mode, with the runs and calls that show it (eval side, never in a room), and one generic finding text per prompt mode (section 8) |
| L5 | The room: `python -m smalls.agent blind-room <dir> --iterate <finding>` writes everything today's room holds, but with the five installed files in place of placeholders and no `prompt-v0.txt`. It adds `FINDING.md` (one finding), `HISTORY.md` (each earlier pass: its finding, the author's `CHANGE.md`, kept or reverted, aggregate numbers), `METRICS.md` (row S7's output) and a brief for iteration. The findings lint refuses a finding that carries a bundle string, a handle, a clock time or a run id, and the overfit reviewer reads the finding before it enters a room. After the pass: `blind-check`, the bundle-string lint, the overfit review of the diff, and the diff's size printed | Tests: a room holds exactly its listed files; a planted finding with a handle, a time or a bundle string is refused; the brief's text is checked like today's |
| L6 | The loop. One pass is: one finding, a room, a fresh sub-agent given only the room's path, install unedited, the regression set (section 7), score, then the keep rule. A pass that is kept also runs the guard set, then commits with the finding's id, the motivating run ids and the result. A pass that is reverted is a `git revert` of its install commit, and the record keeps both. A code finding follows the same keep rule and the overfit review, in its own commit, and is never in the same pass as a prompt change | Each pass is a line in section 9's table: finding, change size, kept or reverted, its numbers, its cost |
| L7 | The retest on the current provider: the frozen candidate's regression and guard sets on the `anthropic` set, scored by the same judge. A second judge from the other provider family scores the comparison runs of both providers, and the two judges' agreement is reported | The default provider is ruled on these numbers and written here, with the cost per run of each |
| L8 | The freeze candidate: the tags ablation (row E5) on it, with tags off and on; E3 if L3 did not settle it; HANDOFF, agent-design section 6 and the write-up's table generated from `eval/runs/`; tag `agent-v1` | PLAN 13's agent bar holds in at least 4 of 5 runs. PLAN 12's gates for a held-out bundle are checked; O4 and P8 (held-out tags) must be settled before h1 |

## 7. The keep rule and the stop rule

Proposed here and fixed in row L3, before the first change, from the measured spread. The defaults below apply until then.

**Regression set:** arm C, the second checkpoint, 5 runs (PLAN 6.1 says 3; see Q2). **Guard set**, run only for a pass that would be kept: each of PLAN step 10's two framing perturbations (an incident commander asserting a wrong cause), 2 runs each through `--trigger`, plus the first checkpoint with carry-forward, 2 runs.

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

**Proposed cap for this plan's live calls: $60.** The parts:

| Item | Estimate |
|---|---|
| Shakedown | about $2 |
| Baseline | about $7 |
| Reverted pass | about $3 |
| Kept pass | about $6 |
| Eight passes | about $35 |
| Retest on the current provider | about $11 |
| Judge calls and overfit reviews | about $3 |

The ceiling probe (about $10) needs a go-ahead of its own. Each live call's cost comes from its own record, with a running total kept here, one line per call. Before a call that could take the total past the cap, the session stops and asks.

Passes, one line each: finding, files changed, lines changed, kept or reverted, regression numbers, guard result, cost, run ids. (Empty until L6.)

## 10. Proposed rulings and open points

| # | Point | Default the build uses |
|---|---|---|
| Q1 | The keep rule and stop rule of section 7 | As written, with the thresholds fixed in L3 from the measured spread |
| Q2 | Regression set of 5 runs, not 3 | 5. With 3, the spread between runs is as large as most fixes; at the second provider's cost, 5 runs cost what 3 did |
| Q3 | The two framing perturbations move from PLAN step 10 into the guard set | Yes. A prompt that has learned this incident shows it when a wrong cause is asserted, and finding that out per pass is cheaper than finding it at held-out. The contrarian control and the alias shuffle stay in step 10 |
| Q4 | The judge on the second provider's flagship tier, pinned, with a second judge for L7's cross-provider comparison | As written |
| Q5 | The tags ablation (E5) runs on the freeze candidate, not now | On the candidate: it measures what ships |
| Q6 | A pass may carry two findings that touch different files, if time is short | No. One finding per pass |
| Q7 | Gate B runs before the baseline | Yes, as the last section 4 note says |
| O4, P8 | How held-out bundles get tags | Still open; must be settled before h1 |

## 11. What this plan does not change

The author rules of G4 (every rule generic, no entity or vendor-specific heuristic, tools described by what they do); the held-out rule; U15; the eval layers and their order; PLAN 12's wording rules for the write-up (counts, never percentages, and never "accuracy", "generalises", "robust" or "validated"); and the rule that development evals are not held-out evidence.
