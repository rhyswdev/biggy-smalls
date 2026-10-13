# AI Engineer Take-Home Assessment: Agentic Incident Investigation System
(Transcribed from the 6-page requirements document, "Biggy AI Engineer - Take-Home Assessment". Everything above "Not in the PDF" is the PDF's text.)

## Overview
We would like you to build an **agentic incident investigation system**.

The system should help investigate messy production issues using operational evidence such as logs, alerts, runbooks, prior incident notes, service metadata, deployment notes, or any other inputs you decide are useful.

The goal is not to build a perfect incident management product. The goal is to see how you think, how you use modern AI tooling, how you design agentic systems, how you handle ambiguity, and how quickly you can turn an open-ended problem into something useful.

This assignment is intentionally loosely defined. We want to see the decisions you make.

## The Goal
Build a system that can take a vague production incident or operational problem and coordinate some form of investigation.

At a minimum, the system should be able to:
- Accept messy operational inputs.
- Investigate what may be happening.
- Maintain some form of evolving investigation state or memory.
- Identify meaningful signals from noisy evidence.
- Form one or more hypotheses about cause, impact, or contributing factors.
- Recommend practical next actions.
- Explain its reasoning using evidence from the provided inputs.

## Suggested Scenario
You may choose your own scenario, but it should be oriented around IT Operations, production reliability, or incident response.

Examples:
- An API latency spike affecting customer traffic.
- A failed deployment causing downstream errors.
- A noisy alert storm where only some alerts matter.
- A service returning intermittent 500s.
- A database, queue, cache, or dependency causing cascading symptoms.
- A recurring incident where prior incident context may matter.
- A messy set of logs and operational notes that need investigation.

The exact scenario is up to you. Make it realistic enough that your system has to reason through ambiguity.

## Inputs
You may create or source your own sample data.

Inputs can include any combination of:
- Logs
- Alerts
- Metrics snippets
- Runbooks
- Prior incident notes
- Deployment notes
- Service metadata
- Ownership information
- Chat transcripts
- Customer impact notes
- Synthetic tool outputs
- Mock API responses
- Knowledge base articles

You do not need a large dataset. A small but realistic dataset with enough ambiguity, noise, and signal is better than a huge artificial one.

## Technical Expectations
Use whatever stack, libraries, frameworks, models, databases, tools, and AI coding platforms you want.

You are encouraged to use modern AI development tools such as Cursor, Claude Code, ChatGPT, Copilot, or anything else that helps you move faster and produce better work.

We are especially interested in seeing how you think about:
- Agent orchestration
- Prompting and prompt design
- Tool use
- Memory / state management
- RAG or retrieval
- Vector databases or semantic search
- Knowledge graphs or structured operational context
- Evidence grounding
- Failure handling
- Practical UX
- Tradeoffs between simple code and agentic behavior

You are not required to use every one of these concepts. Make intentional decisions and be prepared to explain them.

## What to Build
Build something runnable and reviewable.

This could be:
- A web app
- A CLI
- An API/backend service
- A lightweight full-stack application (e.g. a combination of above)

Choose the format[s] that best demonstrates your thinking and engineering ability.

The system should produce a useful investigation output. For example:
- Current incident summary
- Timeline of relevant events
- Key signals found
- Likely hypotheses
- Supporting evidence
- Contradicting evidence
- Open questions
- Recommended next actions
- Confidence / uncertainty
- Suggested stakeholder update or responder notes

You do not need to include all of these. Use your judgment.

## Constraints
This is intentionally open-ended.

We are not prescribing:
- Which language or framework to use.
- Which model to use.
- Whether to use one agent or many agents.
- Whether to use tests.
- Whether to use a vector database, knowledge graph, local files, database, or in-memory state.
- Whether to use real tools, mock tools, or simulated tool outputs.

Make the tradeoffs you think are right.

## What We Care About
We care more about judgment and execution than polish.

Strong submissions will likely demonstrate:
- High agency and strong ownership.
- Clear, practical architecture.
- An eye for design (UI) and an **obsession with UX**
- Good use of **AI coding tools** and prompting.
- Ability to move fast without producing chaos.
- Useful agent behavior, not agent theater.
- Grounded reasoning based on evidence.
- Good handling of ambiguity and uncertainty.
- Practical understanding of IT Operations workflows.
- Clean enough code to understand and extend.
- A system that produces a **genuinely useful** output.

We are not looking for a research project. We are looking for a builder who can take an ambiguous real-world problem and turn it into working software.

## What to Submit
Please submit:
1. A link to your repository or project files.
2. Instructions to run the system locally.
3. A short write-up explaining:
   - What you built.
   - The architecture you chose.
   - How the agentic flow works.
   - How memory/state is handled, if applicable.
   - How retrieval, tools, vector search, knowledge graph, or RAG are used, if applicable.
   - What tradeoffs you made.
   - What you would improve with more time.
4. A sample output from your system investigating the scenario.

## Time Guidance
Use your judgment on scope. We are not looking for a massive project. We are looking for a focused system that shows how you think and how effectively you can build.

A smaller, thoughtful, working system is better than an overbuilt system that does not actually help investigate issues.

## Final Note
Treat this like you are building the first version of a real internal AI SRE incident investigation tool.

The problem is intentionally under-specified. Part of the exercise is deciding what matters, what to ignore, where AI is useful, where deterministic code is better, and how to deliver something valuable quickly.

---

# Not in the PDF

## Context from the cover email
48 hours, starting the morning after receipt. "We are not looking for a massive project, but rather a more focused and useful system."

## Working priorities
Ours, not the PDF's.
- There is no single right answer, so every choice needs a stated reason, and we know exactly what the app does.
- The agent comes first: the agentic system is the brief's first bolded item, and everything else serves it.
- The agent's algorithm, how it works and why each decision was made can be walked through end to end.
