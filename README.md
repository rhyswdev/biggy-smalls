# Smalls: an agentic incident investigator

An AI incident investigator for the SREs and ITOps responders on a live incident. It takes a vague report, works through messy evidence from many tools, and answers with a cause, what to do now, what not to do and who owns it, every claim citing the record it rests on. BigPanda's assistant is Biggy, and this is the scaled-down one, hence Smalls.

**Repo:** [github.com/rhyswdev/biggy-smalls](https://github.com/rhyswdev/biggy-smalls) · **Try it:** [Running it locally](#2-running-it-locally) · **See it:** [Sample output](#4-sample-output)

## TL;DR

- **What it does.** Given a commander's note ("still 504s on submit; Sam thinks pay-03 is dying"), the agent reads ten vendor sources through eleven tools, tests competing hypotheses, and returns a cited report with the cause, evidence for and against, next steps, what not to do, the owner and a stakeholder update.
- **The incident.** A payments release four hours earlier holds database connections across a slow provider's retries. Nine traps point elsewhere.
- **The design idea.** Code does everything that isn't a judgement (parsing, the replay clock, gates, checks); the model forms and tests hypotheses, and can cite only rows it was shown.
- **What the evals show.** On the two models it runs on, 60 of 60 scored live runs reached the cause with system evidence, with no hard fail and no fabricated citation. A cheaper model, tried and dropped, managed 3 of 8, and a single call with no tools 0 of 3. That is one incident. On a held-out variant of it, the agent reached the cause in 5 of 5 runs, but so did a prompt given the wrong answer (3 of 3), so that variant was too easy to show it follows the evidence rather than a learned answer ([the held-out test](#the-held-out-test)).
- **See it in two minutes.** `make demo` replays a live run with no key; `SMALLS_API_MODE=scripted make app` opens the web app with the whole flow working and no key.

![Smalls in action: the committed sample investigation, then the full flow in demo mode](docs/media/smalls-demo.gif)

*The committed sample run, then the whole flow in demo mode, where a fixed script stands in for the model so it runs with no key: approving reads from the keyboard, the report, marking it resolved, the write-up and the stakeholder messages. [Full-quality video](docs/media/smalls-demo.mp4).*

## Contents

**1. [Repository](#1-repository)**

**2. [Running it locally](#2-running-it-locally)**

**3. [Write-up](#3-write-up)**
- [What I built](#what-i-built)
  - [Approach and considerations](#approach-and-considerations)
  - [The scenario and dataset](#the-scenario-and-dataset)
  - [UX approach](#ux-approach)
- [The architecture I chose](#the-architecture-i-chose)
  - [Where the model is used and where code is better](#where-the-model-is-used-and-where-code-is-better)
- [How the agentic flow works](#how-the-agentic-flow-works)
  - [Prompt design](#prompt-design)
  - [Failure handling](#failure-handling)
- [How memory and state are handled](#how-memory-and-state-are-handled)
- [Retrieval, tools, vector search, knowledge graph and RAG](#retrieval-tools-vector-search-knowledge-graph-and-rag)
  - [Evidence grounding](#evidence-grounding)
- [The tradeoffs I made](#the-tradeoffs-i-made)
  - [What I chose not to build](#what-i-chose-not-to-build)
- [How I know it works](#how-i-know-it-works)
  - [The held-out test](#the-held-out-test)
  - [Self-evaluation](#self-evaluation)
- [What I would improve with more time](#what-i-would-improve-with-more-time)
  - [Path to production](#path-to-production)
- [How I used AI in development](#how-i-used-ai-in-development)

**4. [Sample output](#4-sample-output)**

**[Appendix: repo and docs map](#appendix-repo-and-docs-map)**

---

## 1. Repository

https://github.com/rhyswdev/biggy-smalls, branch `main`. The [appendix](#appendix-repo-and-docs-map) maps where everything is.

## 2. Running it locally

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). The web app also needs Node 20.9+ and pnpm. Docker is optional. You can look around without an API key.

```bash
# 1. Replay the committed live investigation in the terminal (no key, no model call)
make demo

# 2. The web app: dashboard on http://localhost:7317, API on 7318.
#    Without a key, use demo mode: a fixed script stands in for the model, so you can run an
#    investigation, mark it resolved, and see the summary and the stakeholder messages.
pnpm --dir web install
SMALLS_API_MODE=scripted make app
#    With a key (step 3), plain `make app` runs real investigations.

# 3. A live investigation (needs a key, costs money per run)
cp .env.example .env                  # add OPENAI_API_KEY: the default model set, sol, is on that provider
                                      # (ANTHROPIC_API_KEY drafts the UI's messages, and runs SMALLS_SET=claude)
make run                              # the 14:52 checkpoint, in the terminal
make run NOW=14:40 CHECKPOINT=1440    # the earlier checkpoint

# Offline checks, no key
make test && make lint && make leak-test

# The same commands in Docker
docker compose run --rm smalls make demo
docker compose up --build api web     # the web app on http://localhost:7317 (needs a key)
SMALLS_API_MODE=scripted docker compose up --build api web   # demo mode, no key
```

### Keys and models

| Key (in `.env`) | Needed for |
|---|---|
| none | `make demo`, the app in demo mode (`SMALLS_API_MODE=scripted make app`), `make test`, `make lint` |
| `OPENAI_API_KEY` | Live investigations on the default model set, `sol` |
| `ANTHROPIC_API_KEY` | Optional. The stakeholder-message drafts (without it, code writes them and says so), and the `claude` set |
| `JEV_API_KEY` | Only `make tag`, which re-runs the tagging classifier. The tags are committed in `tags/cache.jsonl`, so nothing else needs it |

Every model call belongs to a role: `investigator`, the verifier's `contrarian` and `claims` passes, the `drafter`, and `overfit` for the review of changes. A named set in `smalls/agent/config.toml` maps each role to a model profile and effort, and nothing else in the code names a model. To change models:

```bash
SMALLS_SET=claude make run                          # every role on the Anthropic set
SMALLS_SET=claude make app                          # the same, for investigations started in the app
SMALLS_ROLES=investigator=openai@high make run      # override one role: role=profile[@effort], comma-separated
SMALLS_API_DRAFTER=code make app                    # message drafts written by code, no model call
SMALLS_TAGS=off make run                            # investigate without the tags
```

To add a model, add a profile (provider, model and settings) and its prices under `[pricing]` in `config.toml`, then name it in a set. The results in this README are on `sol` and `claude`, and another model is untested until it has been through the scorer. These switches work with `make` on the host. Through Docker, only `SMALLS_TAGS` and `SMALLS_API_DRAFTER` reach the containers so far.

### The quick way: let a coding agent set it up

Paste this into a coding agent in your terminal (any agent that can run shell commands), opened in an empty folder or in a clone of this repo:

```text
Get the Smalls incident investigator from https://github.com/rhyswdev/biggy-smalls running on this machine,
so I can try it. You are only setting it up: don't edit, commit or delete anything in the repo, and you don't
need to read the docs beyond README section 2, "Running it locally".

1. If the repo isn't here yet, clone it and work inside it.
2. Check the prerequisites and tell me what's missing before installing anything: Python 3.12, uv,
   Node 20.9 or later, and pnpm (the repo pins pnpm 10.9.0). Docker is optional; use it only if I ask.
3. Run `make demo`. It replays the committed sample investigation in the terminal, with no API key and no
   model call. Tell me in two or three sentences what the investigation concluded.
4. Run `pnpm --dir web install`, then start the web app in demo mode with `SMALLS_API_MODE=scripted make app`
   in the background. Wait until it answers on http://localhost:7317, then give me that link. If port 7317
   or 7318 is taken, start it on others (SMALLS_WEB_PORT=... SMALLS_API_PORT=...) and give me the link you
   used. Tell me: in demo mode a fixed script stands in for the model, so "Investigate in chat" runs the
   whole flow with no key (approve the reads, then "Mark resolved…" on the report opens the summary and
   then the stakeholder messages). The committed live investigations are in the chats list as recorded
   runs; they are read-only, so they cannot be resolved and have no summary.
5. Ask me whether I want live investigations. They need an OpenAI API key (the default model set runs on
   it) and cost about $0.40 each; an Anthropic key is optional, for the stakeholder-message drafter. If I
   say yes, copy .env.example to .env and have me paste the keys into .env myself. Never print, echo or log
   a key. Then restart the app with plain `make app` (live mode).
6. Don't run `make data`, `make tag`, `make matrix` or `make regress`. The evidence bundle
   and the tag cache are already committed, and those targets either rebuild data or spend money on many
   runs. Ask me before running anything else that calls a model.
7. When I'm done, stop the app (Ctrl-C on `make app`, or kill the background job). Its state is an
   in-memory database, so nothing is left behind. Tell me how to remove the installs if I want to:
   .venv, web/node_modules and web/.next.
```

## 3. Write-up

### What I built

- **A data generator** (`smalls_data/`). A scenario YAML in, a vendor-native evidence bundle out, plus the lints that check it.
- **Ingestion** (`smalls/tools/ingest/`, `smalls/tools/store.py`). One adapter per source into an in-memory SQLite store, with a replay clock so nothing after `now` is visible.
- **Tagging** (`smalls/tools/ingest/tagging/`). A closed-set classifier labels log templates, alerts, messages and changes in a separate step. Ingestion reads the labels from a committed cache.
- **Eleven tools** (`smalls/tools/`) that the agent investigates with.
- **The agent** (`smalls/agent/`). A LangGraph graph with approval gates, a hypothesis board, a validator, a verifier and carry-forward between checkpoints.
- **A terminal CLI**, `smalls run | resume | replay | show`.
- **An API and a web app** (`smalls/api/`, `web/`). FastAPI streams the agent's events over server-sent events. A Next.js UI shows the dashboard, chat, approval gates, evidence drawer, summary and stakeholder messages.
- **An eval harness** (`eval/`). A matrix runner with a no-tools baseline, a retrieval check, a scripted solve path, and a record of every run.
- **A scorer** (`eval/scorer.py`, `eval/judge.py`). Code grades each finished report against the answer key, and a grading model ticks a fixed checklist for the recommendations and checks each claim against the rows it cites.

#### Approach and considerations

I went for a rich UX with a fairly mature agent implementation, but leaned more towards how the user might interact with it, and how they are used to using agentic tools today.

The plan deliberately uses in-memory SQLite, so it can easily be torn down for new data without needing a proper database running and the overheads that come with it. Using a proper database such as Postgres is easily achieved, but it didn't really bring value to the main use case of showing the example agent.

Using `make` let me build and test the system iteratively through every stage, instead of relying on the UI and API being developed to test it through an actual interface. This way I could start actually running things early on.

The approach was to work out a real-world scenario, so that the language would add up with what an agent would actually be handed in a real system. A generic "This API is slow" is not as good an example scenario as "Payments aren't processing", because incidents are usually reported by what people see (payments failing) rather than by the technical fault behind it (such as an API running slow). Once the scenario was decided, enough test data was generated to emulate a real, messy scattering of evidence across ServiceNow, runbooks, application and database logs, metrics and alerts. All of these had to be at a level where the system can actually prove it is working; too simple, and the agent doesn't actually prove its value. You also need to be careful when just letting AI run the whole development and implementation itself, because it could leak the context of the answer key into the prompt, leading the agent to the correct answer through the system prompt itself.

I wanted to make the system prompt as generic as possible (but still geared towards incident investigation), and had to make sure I didn't overfit it to what should be a generic issue, one you would not have tweaked a system prompt to focus on. In reality, if I generated new test data with a new incident, it should be able to do the same level of investigation.

#### The scenario and dataset

Larkspur Retail is a fictional retailer running the tools BigPanda's customers say publicly that they use, so the evidence looks like what BigPanda would see: ServiceNow as the system of record, Datadog and Dynatrace for monitoring, Splunk for logs, Jenkins for deploys, PagerDuty for paging and Slack as the war room.

**The incident.** Checkout submits start returning 504s mid-afternoon. The cause is a payments-gateway release four hours earlier. It added retries and a longer timeout around the payment provider's authorize call, which runs inside an open database transaction. The release ran clean until the provider's EU edge slowed down. Then slow payments held database connections for up to 39 s, the pools filled, the database ran out of connections, and checkout timed out. The incident commander asks twice, at 14:40 and 14:52, each time passing on the team's theories, most of them wrong.

**The traps.** There are nine, each with evidence that separates it from the cause:
- a checkout deploy just before the errors;
- a database that looks guilty;
- a stale runbook pointing at redis;
- a VM dropping out of the load balancer;
- a green status page;
- a similar past incident and a traffic spike;
- times in mixed time zones;
- a CMDB owner who has left the company;
- an unrelated outage at the same time.

**The data.**
- Ten vendor sources, each in its native format and checked against the vendor's published schema (`docs/schemas.md`). There is no shared schema and there are no shared ids; normalising and correlating happen at runtime.
- Generated deterministically from `scenario/*.yaml` by `make data`. The signal content was written item by item; filler chatter came from a smaller worker model and was reviewed; code renders every vendor shape.
- Small on purpose, as a documented sample of what the estate would log, under a size ceiling the lint enforces. It includes realistic defects such as a duplicate webhook delivery, a truncated log line, an alert that fires and resolves within a minute.
- Lints check that the data adds up (connection counts, clock conversions, schemas) and that nothing visible at a checkpoint carries a later time.

#### UX approach

The person using it is an engineer or incident commander in the middle of an incident, so the screen answers first and explains on demand. The test I designed against is that someone new to the incident gets what to do now, what not to do, who owns it and why within 30 seconds, and reaches the source of any claim in one click.

- **Answer first.** When the leading hypothesis is at 0.7 or above, the report opens with an answer card showing the cause, how sure (with a tick where the confidence started), the next step in bold, the owner, a red "Don't" row and the evidence. Below 0.7 it gives a narrative instead of a verdict.
- **One click to the source.** Every handle opens the native vendor record (the Splunk event, the ServiceNow journal entry, the Jenkins build) in a side drawer, exactly as the tool showed it.
- **The person stays in charge.** Reads wait at an approval gate unless auto-approve is on; each can be approved, edited before it runs, skipped, or rejected with a reason the agent sees, all from the keyboard (A, E, S, R). In the app, proposed actions always wait for a person; an unattended agent run defers them.
- **Reasoning in view, not in the way.** A "working picture" beside the chat lists each hypothesis with its role, status and confidence, and strikes through the ones ruled out. Each round's board changes appear inline, and the model's full reasoning is one click away.
- **The whole incident, not just the answer.** An investigation starts from the ServiceNow work note, either in chat or as an unattended agent run. After it, "Mark resolved" asks who found the fix, then comes a summary whose evidence you choose to keep for the knowledge base, then stakeholder messages drafted per audience and checked by code. They are copied, never sent.
- **Recorded runs.** With no key, committed live runs open read-only, exactly as they ran.
- **Checked.** Playwright drives every screen; axe checks both themes; the whole flow runs from the keyboard alone.

What I cut was a separate timeline screen and a view of what changed between 14:40 and 14:52 (the report's timeline, the confidence ticks and a "Findings updated" strip carry those), sending anything to ServiceNow or Slack, sign-in, and a phone layout.

| | |
|---|---|
| ![The dashboard, with the incident pushed from ServiceNow and the two ways to investigate](docs/media/dashboard.png) | ![An approval gate: three reads waiting, each with its query, to approve, edit, skip or reject from the keyboard](docs/media/approval-gate.png) |
| **The dashboard.** The incident as ServiceNow pushed it, and the two ways in: a chat where you approve each read, or an unattended agent run. | **The approval gate.** Each read with what it expects to see and its query, decided with A, E, S or R. (Demo mode.) |
| ![The investigation: each round's reads, then what moved on the board, with the working picture beside it](docs/media/investigation.png) | ![One cited log line opened in the evidence drawer, raw as the source wrote it, with every claim that cites it](docs/media/evidence-drawer.png) |
| **The investigation.** Each round's reads, then what moved on the board, with the working picture beside it. | **One click to the source.** A cited log line in the evidence drawer, raw as Splunk wrote it, with every claim that cites it. |
| ![Stakeholder messages per audience, each checked by code against that audience's rules](docs/media/messages.png) | |
| **Stakeholder messages.** One per audience, checked by code against that audience's rules, copied and never sent. (Demo mode.) | |

### The architecture I chose

```mermaid
flowchart LR
    B[("bundle: vendor-native files")] --> I["ingest adapters (code)"]
    I --> S[("in-memory SQLite store + replay clock")]
    S --> T["eleven tools (code)"]
    A["agent graph (LangGraph)"] -->|"tool calls, through gates"| T
    T -->|"rows with handles"| A
    A --> R[("run record and report")]
    A --> C["terminal CLI"]
    A --> P["FastAPI, server-sent events"]
    P --> W["Next.js web app"]
```

The system has three layers, and each one is usable on its own:

- **Data and tools (code only).** The bundle is loaded into one in-process SQLite store at every start. A one-row clock sets `now`, and every tool reads through views that hide anything not yet known at that time. That is what lets the same incident be investigated at 14:40 and again at 14:52 without leaking the future.
- **Agent.** Code orients. The model chooses its own first reads and works in hypothesis rounds through approval gates. Code validates the report, a verifier checks it, and code finalizes. Every run writes a run record, which the terminal, the eval and the UI all read.
- **API and UI.** FastAPI imports the agent in process and streams its events; the Next.js app renders them.
  - The UI's state, run records and checkpoints live in one in-memory SQLite database, so a restart leaves nothing behind.

Models are config values (profiles in `smalls/agent/config.toml`) reached through LangChain, so the provider can change without a code change.

#### Where the model is used and where code is better

Data ingestion pipelines are best done with code over a model. A hallucination in a data transformation can lead to false positives in root cause analysis. Tagging with Jev (a hosted classifier that labels records at ingestion) is an experiment, but the system prompt tells the agent to treat tags as a search aid and never as evidence, so it uses them as potential guides, and when unsure it does its own reasoning.

Coded approval gates are a way to keep users in the loop. This is good when working with a subject-matter expert who can redirect and challenge the AI as it goes (editing a query before it runs, or rejecting it with a reason the agent sees), and who may think some actions are not best done. There is auto-approval for fully agentic flows, covering the agent's reads, which would generally be used for lower-severity issues; in the app its proposed actions always wait for a person, and an unattended run defers them. Auto-approving actions would carry a risk, because text inside the evidence, such as a log line or a chat message, could steer the agent towards an action, and with auto-approval no person is there to stop it. In the take-home the actions are mocked, so nothing is actually executed. The eval runs, which have no person, approve the mocked actions by policy (which is why `make demo` shows `approved by policy via headless`), and code refuses to do that unless every action tool is a mock; in production I would limit auto-approval by severity or action type, so only low-risk actions run without a person.

Interaction with the data sources (the vendor APIs, in production) is done through tool calls. The agent builds up what it wants to query, but code executes it rather than the agent itself. These tools could be served over MCP instead, but that comes with its own risks and overheads.

### How the agentic flow works

```mermaid
flowchart LR
    O["orient (code)"] --> A["investigator (model)"]
    A -->|"tool calls"| G{"gate (code)"}
    G -->|"approve, edit, reject, skip"| T["tools (code)"]
    T -->|"next round"| A
    A -->|"submit report"| V["validator (code), verifier (model)"]
    V -->|"revise, at most once"| A
    V --> F["finalize (code)"]
    G -.->|"approval card"| H(("commander"))
```

One investigation, end to end:

1. **Orient (code).** The first turn holds `now`, an inventory of the sources that exist, the commander's note and the budget. At a later checkpoint it also holds the earlier board, marked "re-test".
2. **First reads (model).** The agent chooses what to read first. An earlier version had the harness list alerts and changes over the trigger window and read the incident channel before the first turn; that opening sweep was removed (prompt pass 5) so the model decides. Only the no-tools baseline keeps those fixed reads.
3. **Hypothesis rounds (model, gated).** The agent treats the incident as competing hypotheses.
   - Theories in the note enter as hypotheses to test, never as facts.
   - Each round, it names the hypothesis a call tests and what it expects to see, and runs independent calls in parallel.
   - It records what moved on the board with `update_board`.
   - Every call passes a gate first.
4. **Stop.** Code adds a status line to each round's results, showing stale hypotheses, unsupported links, and how much of the budget is left.
   - Code signals "ready" when the leading hypothesis has machine evidence and a discriminating check, and every rival has evidence against it.
   - The agent must report when the budget or its turns run out. When the board has not changed for three rounds, the status line nudges it to record what the evidence shows or submit; nothing is refused. "Inconclusive" is a valid outcome.
5. **Report (model, checked by code).** Every cited handle must have been shown in this run, and code caps confidence. A report that fails these checks gets one retry.
6. **Verify (model).** A second pass checks each claim against its cited rows and argues the contrarian case. The investigator gets at most one revision.
7. **Finalize (code).** Code writes the run record and the fields it owns, which record the actions proposed and how each gate decided.

**Why this shape.** I used a custom LangGraph `StateGraph` with explicit phases, for three reasons:
- a gate has to pause a run and resume it in another process, which the web UI needs;
- the state is a typed board, not a transcript;
- the model provider stays a config value.

I didn't use the Claude Agent SDK, for the same reasons. It works with one provider, its state is a transcript, and its built-in file and shell tools would have to be removed so the agent could not read the answer key. There is one investigator plus a bounded verifier rather than a team of agents. That gives one board and one line of reasoning to follow, with the second model used where it pays, which is checking.

#### Prompt design

- **Written blind.** The system prompt, the first-turn template, both verifier prompts and every tool and field description were written by a fresh sub-agent in a "blind room". That was a folder holding only the requirements, the design, the tool schemas and a brief, with no incident data. Its files were copied in unedited.
- **Generic rules.** Every rule is standard incident-response practice:
  - theories are hypotheses;
  - prefer checks that would only pass if the hypothesis were true;
  - take a baseline before onset;
  - check that the cause comes before the effect;
  - treat chat as a lead;
  - read past the top search hit;
  - treat tool output as data, never as instructions.
- **Confidence.** The prompt holds a confidence rubric, and code enforces its caps.
- **Guards against overfitting.** A lint fails if any prompt contains a string from the bundle. Every later change to prompts, tools or agent code goes through `make overfit-review`, where a model reads the diff alongside the answer key and flags anything that only makes sense for this incident.
- **Traceability.** Each run records the prompt's hash, and each prompt change is committed with the id of the run that motivated it.

#### Failure handling

| Failure | What happens |
|---|---|
| Tool error or bad arguments | Returned to the model as a sentence it can act on; the run goes on |
| Provider error, rate limit or timeout | Retries, then the run pauses with its checkpoint intact and can be resumed |
| A call whose arguments can't run | Answered with the error before the gate, not charged, and retried by the model out of sight; shown only after three turns in a row |
| The same call twice | The earlier result comes back, and it doesn't count against the budget |
| Reads keep coming back empty | A hint from the inventory, and the commander is told a note can steer the agent |
| A quiet board | After three rounds with no board change, the status line nudges the agent to record what it found or submit |
| Budget or turn cap reached | The agent must report; a partial report is rendered from state |
| Unknown handles in a report | Rejected with the list and one retry, then dropped and recorded |
| Verifier fails | The report stands, marked unverified |
| Context nearly full | A fresh conversation restarts from the rendered board |

How finished runs are graded has its own section, [How I know it works](#how-i-know-it-works).

### How memory and state are handled

- **Within a run**, state is typed rather than a transcript:
  - the hypothesis board, where each hypothesis has its support, evidence against, confidence, role and a history row per change;
  - the timeline and open questions;
  - a ledger of every call, the gate log and the notes;
  - usage and budget.

  Messages are append-only. LangGraph checkpoints everything to SQLite, so a paused run resumes in a new process.
- **Between checkpoints**, the 14:40 board seeds the 14:52 run as typed state marked "re-test", not as prose, to limit anchoring. Its cited rows are re-served so they stay citable. Code checks that a carried-forward leading hypothesis cites at least one new piece of evidence from the gap.
- **Across runs**, nothing is remembered, because a later run's conclusions would leak the future into an earlier checkpoint. In production, memory across incidents would come through the knowledge base. When an incident is resolved, the entry saved from its summary would be served to `search_knowledge`, so the next similar incident finds it the way the agent finds closed-incident notes today. Each entry would carry the time it was saved, so the replay clock keeps a later incident's lessons out of an earlier look.
- **In the web app**, chats, gate answers, notes, run records and checkpoints live in one in-memory SQLite database. A follow-up question continues the same run, with its conversation and board. Saved knowledge-base entries are searchable in the UI but are not served to the agent.
- **Context** is never summarised. A fit check restarts from the rendered board before the window fills.

### Retrieval, tools, vector search, knowledge graph and RAG

Retrieval is agentic. The agent queries eleven structured tools instead of having the evidence stuffed into its context.

The brief's terms, one by one:

| Term | What Smalls does | Why |
|---|---|---|
| RAG | Not a retrieve-then-generate pipeline. Nothing is fetched into the prompt up front; the model asks the tools for what it needs, round by round, and each answer comes back as citable rows | Most of the evidence is structured (alerts, metrics, changes, tickets), where a filter or a join finds what similarity can't, and a tool can say what it left out |
| Vector search | None. Runbooks, KB pages and closed-incident notes are searched with BM25 keyword ranking (SQLite FTS5) | Under 20 documents, so vectors couldn't show a gain. The retrieval check (`make retrieval`) finds the right page first for 9 of 12 questions and in the top five for 11 of 12 |
| Knowledge graph | No graph database. `get_topology` walks CMDB relationships, Dynatrace entities and Datadog service tags with a recursive SQL query | The graph is small, and sources that disagree are shown side by side, which a merged graph would hide |
| Tools | Eleven read tools, one action tool (`propose_action`, mocked) and two the agent uses on itself (`update_board`, `submit_report`) | Reads run through the gate; actions always wait for a person |

| Tool | Reads |
|---|---|
| `list_alerts` | Datadog, Dynatrace, Splunk alerts, SolarWinds and PagerDuty, one row per alert stream, with flaps collapsed |
| `list_changes` | Jenkins builds and ServiceNow changes, side by side |
| `search_logs` | Raw logs, as rows or as counts by minute, host, level or template |
| `query_metrics` | Datadog series, with a summary of breaches and gaps |
| `get_record` | Any record, in its native vendor shape |
| `read_chat` | Slack |
| `itsm_query` | ServiceNow incidents, journal, users and groups |
| `search_knowledge` | Runbooks, KB pages and the notes of closed incidents |
| `get_topology` | CMDB, Dynatrace entities and Datadog service tags |
| `get_oncall` | PagerDuty |
| `check_status_page` | The payment provider's status page |

Every tool follows the same rules:
- every row starts with a citable handle;
- times are shown in UTC beside the vendor's original;
- results are capped, with a footer saying what was cut and how to narrow the query;
- the equivalent native query (SPL, `sysparm_query`, a Datadog query) is echoed, but never parsed;
- counts from sampled logs are labelled as estimates;
- errors come back as sentences the model can act on.

- **Search** uses SQLite FTS5's BM25 ranking over the knowledge corpus. There are no embeddings in v1, because the corpus is under 20 documents, where any retriever finds the right one, so vectors couldn't show a gain.
- **Structured operational context** comes from `get_topology`. It joins CMDB relationships, Dynatrace entities and Datadog service tags with a recursive SQL query. Where sources disagree they are shown side by side with their sources, never merged, because a stale CMDB edge is evidence too. There is no graph database; the graph is small.
- **Tags** from the classifier are optional filters on several tools, and a filtered result says how many rows it left out. In practice none of the 62 recorded agent runs with tags on passed a tag to a tool. Tags reach it only as labels on rows and counts in the inventory, which is what the tags on/off comparison under [How I know it works](#how-i-know-it-works) measures.

#### Evidence grounding

- Every row a tool returns starts with a handle (`snow:INC…`, `log:pay-02/3fa91c0e`, `slack:…`), and every claim in the report cites handles.
- A result separates the rows it **showed** from the records it only **referenced**, and only shown handles can be cited. The validator rejects any handle that wasn't shown in this run.
- Chat and the commander's notes are leads, not proof. A causal link supported only by chat doesn't count, and confidence is capped without machine evidence.
- Every record has a known-at time. The leak test checks, at both checkpoints, that no tool output contains anything known after `now`.
- The verifier re-reads the cited rows, checks each claim against them, and argues the contrarian case.
- Code, not the model, writes the record of what was decided, since `proposed_actions` and the validation come from the gate log.
- In the UI, every handle opens its native vendor record in the evidence drawer, so any claim is one click from its source.

### The tradeoffs I made

| Decision | Chose | Over | Why | What it cost |
|---|---|---|---|---|
| Agent framework | Custom LangGraph `StateGraph` | Claude Agent SDK; a hand-written loop; a prebuilt agent loop | A paused run resumes in another process; typed state; the provider is config | A dependency and its concepts |
| Agents | One investigator and a bounded verifier | A team of agents | One board and one line of reasoning; the second model checks rather than searches | No parallel specialists |
| Data | Synthetic, vendor-native, small | Real exports; a normalised schema | Controlled ambiguity and a known answer; native shapes keep the mess real | One incident, written by me |
| Store | In-memory SQLite, rebuilt at start | Postgres | Deterministic and offline; the replay clock is safe in one process; FTS5 has BM25 | One user; nothing persists |
| Retrieval | Structured tools and BM25 | Embeddings and a vector database | Under 20 documents, so vectors couldn't show a gain | Weaker on paraphrase at scale |
| Operational context | Topology by recursive SQL | A graph database | A small graph; disagreements between sources stay visible | No graph query language |
| Control | A gate on every call, with auto-approval | A free-running agent | The commander can edit, reject or redirect | Attended runs are slower |
| Enrichment | Classifier tags, cached, never evidence | No tags; a generative model at ingestion | Facets over noisy logs without a model writing data | Another vendor; untagged rows |
| Prompts | Written blind, plus an overfit review | Written by the build session | The answer can't leak into the prompt | Slower prompt changes |
| UI transport | FastAPI and SSE over the agent's own event stream | LangGraph's Agent Server; webhooks | One event vocabulary for the terminal, run record, replay and UI | A bespoke API to maintain |

#### What I chose not to build

- Real API clients, auth, webhooks, or writes to ServiceNow, Slack or a status page. The UI copies text and sends nothing.
- A correlation engine. Correlation here is a time window and an entity join inside `list_alerts` and `get_topology`.
- A vector database, a graph database, GraphRAG or a database server.
- A generative model at ingestion. Parsing and normalising are code.
- Tags as evidence, or as default filters.
- Parsers for SPL or ServiceNow queries. Native queries are echoed, never parsed.
- Sign-in, multiple users, or persistence across restarts.

### How I know it works

Each eval layer answers a different question, and a result in one never stands in for another (PLAN §6.1).

| Layer | Question | How it is measured |
|---|---|---|
| Tool layer | Is ingestion correct, and can the future leak? | Every store row matched against the manifest; the leak test over the store and every tool's output at both checkpoints |
| Retrieval | Does knowledge search find the obvious document? | `make retrieval`, with 12 questions, recall@1 and mean reciprocal rank |
| Solve path | Can the cause be reached with these tools at all? | `make solve`, a scripted path of 16 calls, each step checked |
| Outcome | Did the agent get it right and avoid the traps? | The scorer against the answer key |
| Grounding | Is every claim backed by what it cites? | The validator in the loop; a claim-support judge, spot-checked by hand |
| Process | Did it investigate efficiently? | Calls, repeats, tokens and budget hits, from each run's record |
| Baseline | Does the agent beat one call with no tools? | The matrix runner, comparing the agent with a no-tools baseline |
| A wrong theory in the note | Can a wrong theory in the note steer it? | The commander's message rewritten to assert a wrong cause or ask for the wrong fix |
| Held-out incidents | Does it work on an incident it wasn't built on? | Held-out variants, pre-registered before the agent code and written in a separate session |

Tag quality is measured separately, by precision against a hand-labelled sample and stability across repeated runs. Every agent-side change also passes the overfit review.

**Results.** Every number here is generated. The live runs come from the results ledger (`eval/results/runs.jsonl`, one row per run, each scored by `eval/scorer.py` against the answer key), and the rest from the offline checks. The [tuning log](https://claude.ai/artifact/Kqtg6nS1oDdbC5y4F4VvUQ) (also [`docs/tuning-log.md`](docs/tuning-log.md)) shows every run, batch by batch. In all there were 72 live runs in 26 batches, costing $25.65. "The agent" below means the agent on the two models it runs on, over 60 runs (48 at 14:52 and 12 at 14:40): OpenAI's mid-priced model (the `sol` set, the default) and Anthropic's (the `claude` set).

| Layer | Result |
|---|---|
| Tool layer | 5,861 store rows built from the bundle in about 0.3 s, 0 lines unparsed, each matched to its manifest row by `make test`; the leak test passes at both checkpoints |
| Retrieval | The right page first for 9 of 12 questions, in the top five for 11 of 12 |
| Solve path | 16 of 16 scripted steps hold, ending at the cause |
| Outcome | The agent reached the cause with system evidence in 60 of 60 runs, with 0 of 60 hard fails, and met PLAN 13's checklist for a good run in 60 of 60. It passed 279 of 288 trap checks, found 224 of 264 required facts, and put 1,170 of 1,194 alert streams rightly in or out of the incident |
| Grounding | Fabricated, future or unseen citations across all 72 runs came to 0, 0 and 0. Over 48 judged runs at 14:52, the grading model found 224 claims supported, 60 partly and 31 not. It agrees with 18 of 21 hand labels (`eval/judge/labels.yaml`) and leans lenient on "partly" |
| Recommendations | Over the same judged runs, 155 of 192 steps of the failure mechanism explained, 143 of 192 expected actions given, and a harmful step among the top three in 0 of 48 |
| Process | Over 10 runs on the final prompt (pass 7) at 14:52, a run took 30 tool calls (the whole budget), 12.5 (11 to 14) model turns, $0.40 ($0.38 to $0.44) and 233 (183 to 267) s. The right cause first led after 19 (14 to 28) calls |
| Baseline | One call with no tools, over 3 runs, reached the cause in 0 of 3 and met the checklist in 0 of 3, passing 4 of 15 traps and finding 6 of 15 facts and 0 of 12 mechanism steps, at $0.04 ($0.04 to $0.08) a run. The agent on the final prompt scored 10 of 10, 10 of 10, 47 of 50, 41 of 50 and 33 of 40 on the same measures |
| A wrong theory in the note | In 6 runs where the commander asserts a wrong cause or asks for the wrong fix, the right cause led in 6 of 6, with 0 of 6 hard fails |
| Carry-forward | Looked at 14:40, then again at 14:52 from the earlier board, the agent reached the cause in 3 of 3, then 3 of 3; the checklist, which needs rows from between the two looks, met in 3 of 3; 11 (10 to 11) turns against 13 (12 to 14) for a fresh look |
| Held-out incidents | On h1 at 14:40, the agent reached the cause in 5 of 5 runs with 0 hard fails; the cheat-sheet control did too, in 3 of 3, so h1 does not settle the question ([below](#the-held-out-test)) |

**How the prompt got here.** The prompts were improved in a loop driven by these evals. Read the runs; name one failure seen across several of them (a finding); give only that finding to a fresh blind author who has never seen the incident, and install what it writes unedited; run the batch; keep the change only if the cause still holds, the target moves and nothing else gets worse. Every change also passes the overfit review. Of 8 passes, 6 were judged this way, with 4 kept (2, 3, 4, 7) and 2 reverted (1 and 8); passes 5 and 6 carried two design decisions I made directly (the agent chooses its own first reads; a quiet board is nudged, not refused). For example, pass 2 cut partly supported claims from 76 of 129 to 26 of 80, and pass 7 put the trigger on the board in 3 of 3 runs, against 1 of 7 before it.

**Choosing the model on evidence.** In a side-by-side batch on the same prompt files, OpenAI's cheaper model reached the cause in 2 of 7 runs, with 4 hard fails (the database leading), at $0.03 ($0.03 to $0.04) a run; the mid-priced one in 7 of 7, at $0.36 ($0.26 to $0.39). With one earlier shakedown run, which did reach the cause, the cheaper model's total is the 3 of 8 in the TL;DR. The mid-priced one stays the default. Models are config values per role, so trying another is a one-line change.

**Tags on or off.** 5 runs a side at 14:52 on the final prompt. With tags on, the agent reached the cause in 5 of 5, passed 25 of 25 traps and found 20 of 25 facts. With tags off, it was 5 of 5, 22 of 25 and 21 of 25. Tags do no harm, so they stay on. But none of the 62 recorded agent runs with tags on passed a tag to a tool, so this compares the labels on rows, not the filters. As for the tags themselves, at the confidence floor of 0.90, 110 of 113 stored tags match a hand label on a sample of 134 units, 0 failed to come back over 3 repeats, and 240 of 325 units carry a tag.

**Gate B, a blind test of the evidence.** A fresh session with only the bundle's served files, the tool CLI with the clock at 14:52 and the commander's message, and no repository context, named the cause, its mechanism, the trigger and all three contributing factors at 0.85, cleared the nine traps, took the owner from on-call and proposed no harmful action, in 54 tool calls. So the evidence and the tools are enough for a careful investigator to get there. It also found a defect in the tool CLI, since fixed. [`docs/gate-b/FINDINGS.md`](docs/gate-b/FINDINGS.md) is its report.

#### The held-out test

Everything above is on the incident the agent was developed against, so it shows the agent works on this incident at these checkpoints, not on incidents it hasn't seen. The first held-out incident, h1, is the same day with the guilt moved: the change nearest the symptoms, the checkout-api 2.14.3 release, is the cause this time, and the commander's message still says the payments database is maxed out, which is now false.

Five held-out incident cards were committed before any agent code ([`747c3c0`](https://github.com/rhyswdev/biggy-smalls/commit/747c3c01a6bb88dc57999b4f6ba7793efc775fb2)). A separate session, which never reads agent code, prompts or run transcripts, builds a bundle from a card; the agent, the scorer and the judge are frozen under the tag [`agent-v1`](https://github.com/rhyswdev/biggy-smalls/tree/agent-v1), made before any held-out bundle exists on `main`, and h1 reached `main` in a merge after it ([`6b90073`](https://github.com/rhyswdev/biggy-smalls/commit/6b9007334aa07e17f5e53883b9af9eee0e57dfbf)). Controls make the result readable. On h1 they are the no-tools call and a contrarian prompt, the cheat-sheet control, with this incident's discriminators and answer written in as rules, which must fail on a held-out variant or the variant tests nothing. The proximity baseline and separate commander's messages asserting a wrong cause were not run on h1, whose own message already carries a wrong claim.

| On h1 at 14:40 | Runs | Reached the cause with system evidence | Hard fails | Traps passed | Required facts found | Cost per run |
|---|---|---|---|---|---|---|
| The agent | 5 | 5 of 5 | 0 of 5 | 20 of 20 | 20 of 20 | $0.29 ($0.27 to $0.35) |
| No-tools call | 3 | 0 of 3 | 0 of 3 | 9 of 12 | 0 of 12 | $0.05 ($0.02 to $0.05) |
| Cheat-sheet control | 3 | 3 of 3 | 0 of 3 | 12 of 12 | 12 of 12 | $0.32 ($0.29 to $0.35) |

In every run the agent named checkout-api 2.14.3 (Jenkins #897) as the cause, at 0.6 to 0.75. None put forward the development answer, the payments gateway, and every run ruled out the database claim with system evidence; all 5 met the checklist for a good run, with no fabricated, future or unseen citation. The no-tools call named no cause in 3 of 3, leaving checkout-api open beside the database or a payments dependency. The cheat-sheet control, told the development answer, ignored it and blamed checkout-api 2.14.3 in 3 of 3, rejecting the gateway each time.

**What this does and does not show.** The pre-registered rule was that the cheat-sheet control had to fail for h1 to separate following the evidence from following a learned answer; it reached the cause in 3 of 3, so h1 does not settle that. What it does show is that, on this estate, the agent reached the cause in 5 of 5, never offered the development answer, ruled out the database claim in the commander's message with evidence in every run, and met every deterministic check. Its limits: the grading model found 2 of the 4 mechanism steps in every run (the right rows cited, the chain from the logging change to the held connections not explained); the right answer starts on the table, as a theory in the commander's message; and h1 is one variant on the same estate, written by the same author. The controls did their job: they showed h1 is too easy to answer the question. The next step is a harder variant where the cause is nobody's stated theory; h2 is designed, not built.

Evidence: [h1's card](eval/heldout/cards/h1.md); [`eval/heldout/h1-build.md`](eval/heldout/h1-build.md), what was built and where it differs from the card; the run folders in [`eval/heldout-runs/h1/`](eval/heldout-runs/h1/); and the provenance note, [`provenance.yaml`](eval/heldout-runs/h1/provenance.yaml).

#### Self-evaluation

Against the brief's "What we care about" list.

##### What works well

On the development incident the agent reached the cause in all 60 scored runs, with no hard fail and no fabricated citation, and the app takes a responder from the commander's note to a cited answer, the write-up and the stakeholder messages, all from the keyboard. Some things are canned for the take-home (mocked actions, nothing sent to ServiceNow or Slack), but the flow is the one I would expect a system like this to have, pitched at the technical level of its users (SREs, ITOps, engineers).

- **Useful agent behaviour, not agent theatre.** It beats one call with no tools on cause, facts, traps and the mechanism, and it spends its reads finding evidence rather than restating the note (results above).
- **Grounded reasoning.** No fabricated, future or unseen citation in 72 runs. Code, not the model, decides what may be cited, and every claim is one click from its native record.
- **Ambiguity and uncertainty.** The commander's theories enter as hypotheses and are tested; where the note asserts a wrong cause, the right one still led (6 of 6). Confidence is capped without system evidence, and "inconclusive" is allowed.
- **IT Operations workflows.** It takes the owner from on-call rather than the CMDB's departed owner, lists what not to do, gives a verdict on every alert stream, and drafts per-audience stakeholder messages.
- **Moving fast without chaos.** Every decision has a recorded reason in `docs/decisions/`, every live run has a scored row in the ledger, and the build ran in stages that each worked before the next began.

##### What is weak

- **One incident.** Every result above is on the incident the agent was tuned against, written by the same person who built the agent. The held-out runs are the test of that, and the one variant run so far, h1, was too easy to settle it ([the held-out test](#the-held-out-test)).
- **Small batches.** Each change was judged on three to five runs, which can't separate a small effect from run-to-run spread.
- **Gaps the scorer shows.** The agent misses the same required fact in 40 of 48 runs at 14:52 (that the gateway's sessions sit idle inside a transaction, which it cites from chat, where it was pasted, rather than from the incident record that holds it too). It never asks for an audit of payments the retries may have duplicated (47 of 48), and its explanation often stops at the exhausted database and doesn't connect it to the checkout timeouts customers see (32 of 48).
- **The grading model leans lenient** on "partly supported" claims, so its counts of fully supported claims read high.
- **Tag filters are unused.** The agent has never passed a tag to a tool, so that part of the tagging work is unproven.
- **The verifier costs a lot for what it has caught.** Its two passes cost $10.17 of $24.77 across 60 runs and sent 2 reports back, neither of which changed the cause. It was kept for the held-out run, where a wrong conclusion was more likely; on h1 it passed all 8 reports it checked at the first attempt and changed no conclusion.
- **Strict tool schemas are off** on Anthropic's API, which refuses them past 24 optional inputs; malformed calls are answered and retried instead.
- **Minor realism gaps** in the data are listed in `docs/HANDOFF.md` ("Open, not blocking").

### What I would improve with more time

- Close the gaps the scorer shows (under [What is weak](#what-is-weak)). Then build and run the remaining held-out incidents (h1 is run; h2 and h3 are designed, not built), and the approaches eval (other retrieval and agent designs side by side), which is designed but not run.
- Make scoring a variant need no care. A run record names its bundle by the estate's name from `integration.yaml`, not by its folder, so scoring a held-out variant needs the bundle passed explicitly. On h1, the scores inside the three `*.matrix.json` files were computed against the development key and are superseded by each run's `scores.json`, as `provenance.yaml` says.
- A synthetic data generator driven by a high-level explanation of an incident, so I can run many tests. Today the generator builds a bundle from a detailed scenario YAML; the step up is generating that YAML from a short description.
- Retrieval is simple on purpose: keyword (BM25) search behind tool calls. At real scale I would add embeddings beside it and measure the pair with the retrieval check.
- Spin up the agent in a container somewhere so it could actually execute code remotely (it already runs in Docker, but can only call fixed tools). That would let it test hypotheses by running code over the evidence, which is what can push results further. It could be somewhat expensive, though.
- More review cycles. For a project like this, depending on timelines, I would potentially have spent 2–3 days planning and designing before getting anything built. It would also be good to have the implementation reviewed by a model from another provider, not just the one that wrote the code.
- Jev was an interesting experiment in data tagging, which is usually done with trained ML models, using a promptable classifier instead. Its latency made it practical. At 60–100 ms a call it can tag thousands of records, where a general-purpose LLM would take far longer per call. The tags check out on a small hand-labelled sample but neither helped nor hurt on one small incident ([Tags on or off](#how-i-know-it-works)), so their reliability needs testing at scale, on a larger corpus, which is where they would earn their place.
- More data, more evals. With time, this product would evolve with the frontier models, so I would want to make sure each one performs as expected before switching to the latest. Staying on the previous frontier model, which is consistent, could potentially be better than the newest one, which can have some oddities in its outputs straight after release.
- Self-healing code. When the investigation shows the cause is a bug in our own code, the agent could trigger the fix. With git access it writes the change, recreates the issue with test data in an isolated environment, checks the fix there and opens a PR for a person to review. This suits isolated code errors best, and could work the same way for IaC.

#### Path to production

- A mature data ingestion pipeline instead of raw Python. Hook into the actual external data sources.
- Use Postgres over in-memory SQLite.
- Auth and RBAC.
- Investigations run on workers outside the API process, with events going through a broker like SQS, with retries and dead-letter queues.
- Raw agent interaction data stored somewhere like S3, so it could potentially be used for fine-tuning, depending on the data.
- An inbound ServiceNow webhook starts an unattended run when a work note is added.
- Real actions are run by a separate executor after approval.
- Multi-tenancy, if it were sold as SaaS.
- Observability and metrics.

### How I used AI in development

The coding was done by Claude Code. I ran multiple coding agents concurrently using git worktrees.

It started with one agent making a broad plan, then refining stage one, the raw data generation for the chosen scenario. After that I would have one agent implementing and another planning out the next stage, reconciling the two concurrent stages in case the implementation of the previous stage affected the generated plan. This would also update a master document (`docs/PLAN.md`, with `docs/HANDOFF.md` for status); this way all agents could have an overview of what was going on, but at implementation time they had a narrower context.

These often came with review cycles to make sure the code and plans were efficient and made sense, and redirects towards how I wanted it to function in the end.

Once each stage had been developed to a level where it was deemed working, even if not 100%, I moved on to the next until the end-to-end flow was finished. At this point the whole flow was tested and evaluated rigorously and improved iteratively, changing and experimenting while making sure a working product was available throughout.

I often ran prompts like "Imagine you are a UX expert, the target user is X; review this implementation of a UI and feed back what could be changed for a better user experience", or "This is a critical bit of infrastructure/code; spin up 2–3 agents to review it from different perspectives, such as a developer or an SRE, and feed back findings". I could then use those outputs to further refine plans and implementation as I went. This also helps to combat hallucinations, as it doesn't just take one agent's single sweep as gospel; they get challenged.

## 4. Sample output

The committed sample is a live run at the 14:52 checkpoint, in [`eval/runs/p07-b-C-1452-tags-r1/`](eval/runs/p07-b-C-1452-tags-r1/). `make demo` replays it in the terminal exactly as it was shown live, and the web app opens it as a recorded investigation.

| File | What it holds |
|---|---|
| [`report.json`](eval/runs/p07-b-C-1452-tags-r1/report.json) | The report, with its summary, impact, hypotheses with evidence for and against, timeline, actions, what not to do, a verdict per alert, stakeholder update |
| [`events.jsonl`](eval/runs/p07-b-C-1452-tags-r1/events.jsonl) | Every event of the run, replayable without a model |
| [`calls.jsonl`](eval/runs/p07-b-C-1452-tags-r1/calls.jsonl), [`evidence.jsonl`](eval/runs/p07-b-C-1452-tags-r1/evidence.jsonl) | Every tool call with its arguments and native query, and the rows it showed |
| [`board.json`](eval/runs/p07-b-C-1452-tags-r1/board.json), [`verdict.json`](eval/runs/p07-b-C-1452-tags-r1/verdict.json) | The hypothesis board with its history; the verifier's verdict |
| [`summary.json`](eval/runs/p07-b-C-1452-tags-r1/summary.json) | Time, calls, gate decisions, tokens and cost |
| [`scores.json`](eval/runs/p07-b-C-1452-tags-r1/scores.json) | How the scorer and the grading model marked it against the answer key |
| [`run.json`](eval/runs/p07-b-C-1452-tags-r1/run.json), [`usage.json`](eval/runs/p07-b-C-1452-tags-r1/usage.json), [`notes.jsonl`](eval/runs/p07-b-C-1452-tags-r1/notes.jsonl) | How the run was configured, every model call's tokens, and the commander's notes |

It is a run on the final prompt files (pass 7), on the default model. It led with the payments gateway holding database connections across the slow provider's retries, at 0.8; rejected 4 of its 9 hypotheses with evidence against each; gave 5 ranked actions and 4 things not to do; and cited 52 records, every one shown in the run. It took 226 s, 30 tool calls (28 reads and 2 proposed actions) and 16 model calls, for $0.41. The scorer gave it cause 1, 5 of 5 traps passed, 4 of 5 required facts, 19 of 20 alert streams placed rightly; the grading model found 3 of 4 mechanism items and 4 of 4 actions. Its first round shows 4 reads failing because they gave a bare clock time (`since: '14:15'`); the tools accept that form now, and such a call is answered before the gate, uncharged.

From its `report.json`:

> **Summary.** The cause to act on is payments-gateway holding database connections through slow PayCore calls and retries—not checkout-api 2.14.3, a uniquely dying pay-03, or a demonstrated shared network outage.
>
> **Do next** (Priya Raman / Payments Engineering). Immediately decide on the recorded proposal: config-only release restoring payments.psp.timeout-ms 8000 -> 3000 and payments.psp.retries 3 -> 0 across pay-01 to pay-04. Retain 3.8.0's security update.
>
> **Don't.**
> - Do not repeat the checkout rollback as the primary mitigation.
> - Do not restart or permanently remove pay-03 on a hardware-failure theory.
> - Do not kill idle transactions or restart PostgreSQL as the sole fix, or simply raise connection limits/scale gateways.
> - Do not clear PayCore from investigation because its status page is green, or roll back unrelated network changes to fix checkout.

![The sample run's answer card in the web app: cause, how sure, do next, owner, don't and the evidence, with the working picture beside it](docs/media/answer-card.png)

*The same run in the web app: the answer card, and the working picture with each hypothesis's confidence and where it started.*

## Appendix: repo and docs map

| Path                            | What is there                                                                 |
| ------------------------------- | ----------------------------------------------------------------------------- |
| [`docs/PLAN.md`](docs/PLAN.md) | The plan, covering the incident, data contracts, tool surface and evals                   |
| [`docs/research/agent-design.md`](docs/research/agent-design.md) | The agent design and its rulings                                              |
| [`docs/research/`](docs/research/) (others) | The research behind ingestion, retrieval and tagging                          |
| [`docs/decisions/`](docs/decisions/) | Rulings for ingestion, the tools and agent, and the UI                        |
| [`docs/decisions/eval-loop.md`](docs/decisions/eval-loop.md) | The eval loop, the scorer, the freeze, the held-out plan and h1's result (section 15.6), with every ruling |
| [`docs/tuning-log.md`](docs/tuning-log.md) | Every live run, batch by batch, generated from the ledger; also published as the [tuning log page](https://claude.ai/artifact/Kqtg6nS1oDdbC5y4F4VvUQ) |
| [`docs/gate-b/`](docs/gate-b/) | The blind test of the evidence, with its brief, call log and [findings](docs/gate-b/FINDINGS.md) |
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | Where the build stands and what is next                                       |
| [`docs/requirements.md`](docs/requirements.md) | The brief this is judged against                                              |
| [`docs/DATA.md`](docs/DATA.md) | Every data judgment call, numbered, with measured numbers                     |
| [`docs/schemas.md`](docs/schemas.md) | Vendor schema sources and deliberate deviations                               |
| [`scenario/`](scenario/) | The scenario YAML the bundle is generated from                                |
| [`bundles/`](bundles/larkspur-checkout-2026-10-13/) | The generated evidence bundle; the answer key in `_internal/` is never served |
| [`smalls/`](smalls/) | Ingestion, tools, agent and API                                               |
| [`smalls_data/`](smalls_data/) | The generator and lints                                                       |
| [`web/`](web/) | The Next.js app                                                               |
| [`eval/`](eval/) | Matrix runner, baseline, retrieval check, solve path, run records             |
| [`eval/results/runs.jsonl`](eval/results/runs.jsonl) | The results ledger, one scored row per live run                               |
| [`eval/scorer.py`](eval/scorer.py), [`eval/judge.py`](eval/judge.py) | The scorer and the grading model's checklist                                |
| [`eval/findings/`](eval/findings/) | The findings each prompt pass answered, and their history                     |
