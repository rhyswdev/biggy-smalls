# Smalls

Take-home for an AI Engineer role at BigPanda. Their AI incident assistant is Biggy; ours is the scaled-down one, so: Smalls. An agentic incident investigation system over messy, raw, multi-source operational evidence.

Start here: `docs/HANDOFF.md` for where the build stands and what is next, then the `docs/PLAN.md` sections the task needs. PLAN is self-contained and holds every decision made so far, the incident, the data contracts, the tool surface and the build sequence.

Docs (all under `docs/`):

- `PLAN.md`: the plan. `HANDOFF.md`: status and next step; update it when a step lands.
- `requirements.md`: the assessment we are judged against, and our working priorities.
- `DATA.md`: data judgment calls, numbered. `schemas.md`: vendor schema authorities and kept deviations.
- `decisions/`: rulings (`step6-decisions.md`: step 6 tool layer; `ingestion-decisions.md`: step 6a ingestion and tagging; `tools-agent-build.md`: the tools and the agent, with the build order, each row's acceptance evidence and every judgment call). `research/agent-design.md`: the settled agent design and its rulings; `research/ingestion-and-retrieval.md`: the research behind the tool layer; `research/tagging.md`: the research behind the tagging pass.

Doc rules: no markdown outside `docs/` unless something reads it (`scenario/content/` is generator input; `spikes/agent/README.md` belongs to the spike; `eval/heldout/` is the held-out session's). Fold a review's findings into PLAN, DATA or a decision record, then delete the review; git keeps it. Write docs as a shared working document: record what was decided and why, in the first person plural, not who said what; name no outside people.

Conventions:

- Every timestamp, count and name in a bundle derives from `scenario/*.yaml` through the generator. Never hand-edit a generated file under `bundles/`.
- Fixtures stay in each vendor's native shape, validated against the authorities in `docs/schemas.md`. Normalising and correlating happen in `smalls/tools/` at runtime.
- `_internal/` (manifest, answer key) and `scenario/content/` are never served to the agent.
- The agent's prompts must not contain bundle-specific strings (PayCore, Hikari, pay-db, 3.8.0, Larkspur). A lint checks this.
- The store is in-process SQLite, rebuilt at every start; there is no database service. `compose.yaml` runs the app service, and later services join it; `uv` on the host stays the primary path.
- Tags: `make tag` is the only command that calls the classifier, and it needs `JEV_API_KEY`. Ingest reads the committed `tags/cache.jsonl` and never the network. Tag profiles are prompt text, so the bundle-string rule and the held-out rule both apply to them. A tag is a query facet, never evidence, and is applied only at high confidence. Tags are on by default (ruling G6 in `docs/decisions/tools-agent-build.md`); `SMALLS_TAGS=off` turns them off.
- Held-out rule: a session that writes or edits the agent prompt, the agent loop, `smalls/tools/` (tag profiles included), the scorer or the judge prompt must not read `eval/heldout/`, `scenario/variants/`, or `bundles/h*/`. Held-out data is authored in a separate session that does not read agent code, prompts or run transcripts. See `eval/heldout/README.md`.
- Blind prompts: the four files under `smalls/agent/prompts/` and `smalls/tools/descriptions.yaml` are written in a blind room, by a fresh sub-agent that is given only the room's path. A session that has seen the bundle never writes or edits them: it runs `python -m smalls.agent blind-room|blind-check|blind-install` and copies the result in unedited. A change to one is a new blind pass. Section 3 of `docs/decisions/tools-agent-build.md` has the rules.
- Default stack is Python 3.12 with `uv`; `make data`, `make lint`, `make ingest`, `make tag`, `make leak-test`, `make tools-smoke`, `make retrieval`, `make solve`, `make score`; and, live and costing money, `make run`, `make resume`, `make regress`, `make matrix`.
- Branch: `main`. Commit small, push often; the container is ephemeral.
- Design model-agnostic: the agent model and the classifier version are config values. Code, prompts and docs refer to them by role and assume no particular model.
