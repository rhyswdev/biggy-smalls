# Held-out evaluation set

**Handling rule.** Nothing in this folder, in `scenario/variants/`, or in `bundles/h*/` may be read by a session that writes or edits the agent prompt, the agent loop, `smalls/tools/`, the scorer or the judge prompt. The held-out claim rests on that. A separate session authors held-out data from these cards; it may read the cards, `scenario/facts.yaml`, the base scenario, the generator and the lints, and may not read agent code, prompts or run transcripts.

**Shape.** Not a difficulty ladder. Each held-out variant keeps the estate, the noise, the tools and roughly the development bundle's difficulty, and makes at least one lesson of the development bundle false, so a failure points at the lesson the prompt over-learned. The lesson each card falsifies is stated inside the card only.

**Pre-registration.** Every card and `criteria.yaml` are committed before the agent exists. The write-up quotes the commit hash. A variant whose card or key changes after `agent-v1` is reported as development data, not held out.

**Cards.** `h1` and `h2` are same-day variants of the Larkspur incident (cheap: signal events change, estate does not). `h3`, `h4` and `h5` are new-day incidents in the same estate, specified so they can be built later or cited in the write-up as designed, not built. `designs.md` holds their full designs (timelines, signal items, realism checks, tool needs); the cards are the pre-registered summary. Priority if time allows after the `v1-submittable` tag: h1, then h2, then h3. h4 and h5 are cards only unless everything else is done.

**Acceptance before the agent ever runs on a variant.**
1. `make data`, `make lint`, `make leak-test` pass for the variant; the served-output lint finds no bundle id, path or variant name in any tool result.
2. The scripted solve path in the card reaches every required fact within the card's call budget.
3. The scorer separates two hand-written reports: the correct one and one that names the development answer.
4. The no-tools baseline fails the cause match in at least 2 of 3 runs.
5. The contrarian control (the development bundle's discriminators written as rules in a throwaway prompt) fails the cause match, or names the development answer, in at least 2 of 3 runs. Read only the scorer verdict lines; do not open those transcripts before the held-out run.
6. No new sources, tools or vendor shapes. Entities and people come from `facts.yaml`.

**Freeze and run.** Tag `agent-v1` covering prompts, tool code, loop, report schema, scorer, judge prompt and `criteria.yaml`. `make heldout` refuses a dirty tree or a tree that differs from the tag, runs each cell once (5 runs for headline cells, 3 for the rest), stamps each run file with the tag, the bundle content hash and the model settings, and appends to `runs/`. Crashed or schema-invalid runs count as failures. The results table in the write-up is generated from `runs/`, unedited. If the agent changes afterwards, tag `agent-v1.1`, re-run, and show both.

**Reporting.** Counts, never percentages or totals across bundles. Never the words accuracy, generalises, robust, validated. The safe sentence: "Within one estate, the verdict follows the evidence when the evidence changes, and the controls show this test can catch a prompt that memorised the development incident."
