# Smalls

Take-home for an AI Engineer role at BigPanda. Their AI incident assistant is Biggy; ours is the scaled-down one, so: Smalls. An agentic incident investigation system over messy, raw, multi-source operational evidence.

Start here: `docs/HANDOFF.md` for where the build stands and what is next, then the `docs/PLAN.md` sections the task needs. PLAN is self-contained and holds every decision made so far, the incident, the data contracts, the tool surface and the build sequence.

Docs (all under `docs/`):

- `PLAN.md`: the plan. `HANDOFF.md`: status and next step; update it when a step lands.
- `requirements.md`: the assessment we are judged against, and our working priorities.
- `DATA.md`: data judgment calls, numbered. `schemas.md`: vendor schema authorities and kept deviations.
- `decisions/`: rulings (step 6 tool layer). `research/agent-design.md`: the settled agent design and its rulings; `research/ingestion-and-retrieval.md`: the research behind the tool layer.

Doc rules: no markdown outside `docs/` unless something reads it (`scenario/content/` is generator input; `spikes/agent/README.md` belongs to the spike; `eval/heldout/` is the held-out session's). Fold a review's findings into PLAN, DATA or a decision record, then delete the review; git keeps it. Write docs as a shared working document: record what was decided and why, in the first person plural, not who said what; name no outside people.

Conventions:

- Every timestamp, count and name in a bundle derives from `scenario/*.yaml` through the generator. Never hand-edit a generated file under `bundles/`.
- Fixtures stay in each vendor's native shape, validated against the authorities in `docs/schemas.md`. Normalising and correlating happen in `smalls/tools/` at runtime.
- `_internal/` (manifest, answer key) and `scenario/content/` are never served to the agent.
- The agent's prompts must not contain bundle-specific strings (PayCore, Hikari, pay-db, 3.8.0, Larkspur). A lint checks this.
- Held-out rule: a session that writes or edits the agent prompt, the agent loop, `smalls/tools/`, the scorer or the judge prompt must not read `eval/heldout/`, `scenario/variants/`, or `bundles/h*/`. Held-out data is authored in a separate session that does not read agent code, prompts or run transcripts. See `eval/heldout/README.md`.
- Default stack is Python 3.12 with `uv`; `make data`, `make lint`, `make leak-test`, `make score`.
- Branch: `main`. Commit small, push often; the container is ephemeral.
- Do not put model identifiers in commits, code comments or docs.
