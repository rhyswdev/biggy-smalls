# Earlier revision passes

Each pass changed the five files to fix one generic finding, and was kept or reverted on the numbers from runs before and after it.

## Pass 1: board updates sent in turns of their own (reverted)

**Finding.** Every board update went in a turn with no reads beside it, about a third of each run's turns.

**Change.** The board section gained a paragraph saying to send the update first, in the same turn as the round's reads, and never alone, with the reason. The budget paragraph and the first-turn template called board updates "costing no call" instead of "free", and said every turn counts as a model turn. The stopping rule gained a clause for a turn whose update makes the board ready. The `update_board` description said the same.

**Result: reverted.** Afterwards every board update still went in a turn of its own: five such turns in each of three runs, against four to six before. Conclusions, calls used and cost were unchanged. The model alternates reads and board updates turn by turn whatever the wording, so this finding is set aside for a change outside the prompt. A new pass should not restate that rule.

## Pass 2: claims said more than the rows they cite (kept)

**Finding.** Over half of each report's claims were judged `partial`. Timeline rows compared their event with others they did not cite, hypothesis statements named effects no support row showed, and do-not reasons and actions added predictions.

**Change.** The citation rules now say a claim says only what its own rows show: a row for each part, both rows for a comparison, reasoning worded as reasoning, and an unsupported prediction left out while the claim stays. `support` holds a row for each link the statement names, the effect included, and the discriminating rows. Timeline rows hold one event each. The matching field descriptions say the same.

**Result: kept.** Partial claims fell from 59% to 32%, with the same conclusions and about the same number of claims per report. Cost per run was a little higher and wall time a little lower.

## Pass 3: hypotheses left open with nothing said (kept)

**Finding.** A hypothesis at the bottom of the scale, with only rows against it, was left open in the report with no open question about it.

**Change.** One rule in the board section, and the matching text on `status`: by the report, an `open` hypothesis with no support of its own is either rejected, when its `against` rows clear it, or named in an open question that says what check would decide it.

**Result: kept.** Afterwards every such hypothesis carried an open question naming the check that would decide it. Conclusions were unchanged and cost was a little lower.

## Pass 4: the commander's request was argued against but not answered (kept)

**Finding.** When the commander asked for a step or stated a cause, the report made its own case without answering the request as asked. One report declined a requested plan outright. Factual questions were already answered first and plainly.

**Change.** The first-turn template says what an answer is for each kind of request: a question gets its answer; a step or plan gets what it would involve and what it would and would not fix; a stated cause gets whether the evidence supports it, and why. The `summary` opens with that plain answer, then the cause and confidence. It never declines or reframes a request in place of answering it, and the recommendation may differ.

**Result: kept.** Afterwards, a stated cause was answered "no" with the reason in the first sentence. A requested plan was given in full, alongside the evidence that it would not help. Stated causes were still tested, not adopted, and the leading hypothesis was unchanged.

## Pass 5: the harness read alerts, changes and chat before the agent's first turn (installed, not yet measured)

**Finding.** The harness ran a fixed opening, the alert streams and changes around the trigger and the recent chat, before the agent's first turn. That assumed alerting covers the fault and works, which it often does not, and it took the choice of where to start away from the agent.

**Change.** The harness now reads nothing first. The system prompt and the first-turn template say the agent decides where to start, from the trigger and the inventory, with independent reads together in its first turn; that alerts, changes and chat are often useful but not assumed; and that a quiet alert list does not mean nothing is wrong. The alert-verdict text asks for a verdict on every stream the agent's own `list_alerts` calls showed.

**Result: installed, not yet measured.** It changes what every run does in its first turn, so the earlier passes' numbers are not a baseline for it.

## Pass 6: a quiet board was refused, and the prompt said so (installed, not yet measured)

**Finding.** After three rounds with no board change, the harness refused every read of the next round. That cost a turn and showed the commander refused calls. The count also ran while the board was still empty, which is likelier now that the agent chooses its own first reads. The harness now nudges instead: after three quiet rounds, empty board or not, the status line carries one more part before the budget, and nothing is refused.

**Change.** In the system prompt only: the status-line description lists the new part in its place, the stale rule no longer says the gate refuses reads, and one rule says what the part means and what it asks for (record what the reads so far show, or submit), with the reason. The round count is written as `<n>`, as the line prints it, so the prompt cannot drift from the setting again.

**Result: installed, not yet measured.** The next runs are on new models, so they are the first measure of it.

