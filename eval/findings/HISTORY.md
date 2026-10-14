# Earlier revision passes

Each pass changed the five files to fix one generic finding, and was kept or reverted on the numbers from runs before and after it.

## Pass 1: board updates sent in turns of their own (reverted)

**Finding.** Every board update went in a turn with no reads beside it, about a third of each run's turns.

**Change.** The board section gained a paragraph saying to send the update first, in the same turn as the round's reads, and never alone, with the reason. The budget paragraph and the first-turn template called board updates "costing no call" instead of "free", and said every turn counts as a model turn. The stopping rule gained a clause for a turn whose update makes the board ready. The `update_board` description said the same.

**Result: reverted.** Afterwards every board update still went in a turn of its own: five such turns in each of three runs, against four to six before. Conclusions, calls used and cost were unchanged. The model alternates reads and board updates turn by turn whatever the wording, so this finding is set aside for a change outside the prompt. A new pass should not restate that rule.
