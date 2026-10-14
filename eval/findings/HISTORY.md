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
