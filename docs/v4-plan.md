# v4 UI redesign: plan

The approved design is `design/v4/` (one `*.dc.html` per screen, `EmptyV4.dc.html` added after the first review, intent in `design/v4/NOTES.md`, tokens in `design/v4/ds/tokens.css`). This plan maps what we have to what v4 needs, says what we reuse and what we build, and records the judgment calls the brief leaves open. It is a presentation pass: agent reasoning, check choice, confidence scoring, approval semantics and the gate's keys stay as they are.

Status: reviewed 2026-10-20; the three questions in section 9 are answered and folded in. Built 2026-10-20 to 21, row by row; what changed and why is in `docs/decisions/ui-build.md` section 13.

## 1. Rules that still bind

These come from `docs/decisions/ui-build.md` and CLAUDE.md, and the v4 work keeps them.

- **U7, boundary.** Every `page.tsx` and `layout.tsx` stays a server component with no client logic; interactive parts are client components under `web/components/`; mutations are server actions; no `app/api/`. `web/scripts/check-boundary.mjs` keeps enforcing it.
- **U10, no sample data in code.** The design's copy is final for labels, headings and sentence frames. Its incident content (names, CHG numbers, "1 in 6", "41 s", Slack IDs, phone numbers, "PAY-2231", the CI map's nodes) never enters code, fixtures or tests. Every incident fact comes from served data at `now`, from a run, or from the generated overview (section 5.2). The bundle-string lint (invariant 17) keeps scanning `web/` and `smalls/api/`.
- **U15, overfit review.** Any change under `smalls/agent/`, `smalls/tools/`, `smalls/contracts/` or `smalls/api/prompts/` passes `make overfit-review` before commit.
- **Blind files.** The four agent prompts and `smalls/tools/descriptions.yaml` are not edited. Section 5.1 is designed around that.
- **Held-out rule.** This work touches the API's prompts, so the session doing it does not read `eval/heldout/`, `scenario/variants/` or `bundles/h*/`.

## 2. Screen map: what exists, what it becomes

| v4 screen | Today | Route after | Reuse | Build |
|---|---|---|---|---|
| EmptyV4 (first run) | nothing: `/` always shows the dashboard | `/` when no incident is imported | Button, the import action | `firstrun/FirstRun.tsx`: two-line welcome, the two choices as large cards (Import from service desk marked "Start here", with the number field and the open tickets not yet imported; Start a chat), the three-step "How it works", and the connection line. No sidebar |
| SidebarV4 + NewIncidentV4 (shell) | `shell/AppBar.tsx` (top bar, Dashboard and New chat links), `ThemeSwitch` | layout keeps the server shell; sidebar is a client island | ThemeSwitch, Button, StatusTag | `shell/Sidebar.tsx` (296px, steps, "after resolving" state, Earlier list), `shell/NewIncidentMenu.tsx` (menu + Import dialog), `shell/IncidentHeader.tsx` (P1, Major incident badge, title, state tag, meta line, View ticket), `shell/StatusBanner.tsx`, `shell/TicketView.tsx` (drawer and embedded) |
| IncidentV4 (overview) | `app/page.tsx` dashboard: `IncidentCard`, `AgentPanel`, `ChatsList`, `KnowledgeBand` | `/` (the open incident) | AgentPanel's three states (running steps, stopped, finished) restyled as the "Smalls diagnosis" panel | `overview/WhatsGoingOn.tsx` (two sentences + key-facts strip), `overview/CiMap.tsx`, `overview/Theories.tsx`, `overview/Timeline.tsx`, `overview/Glossary.tsx`, `overview/Contacts.tsx`. `IncidentCard`, `ChatsList`, `KnowledgeBand` retire (chats move to the sidebar) |
| ChatV4 (diagnosis) | `chat/ChatScreen.tsx`, `Gate.tsx`, `AnswerCard.tsx`, `WorkingPicture.tsx`, `IncidentBar.tsx` | `/chats/[id]` | The reducer (`lib/chat/reduce.ts`) and the event hook unchanged; Gate's row logic, keys, compare-and-set and focus model unchanged; AnswerCard's data picks; WorkingPicture's content; the evidence drawer | Gate's waiting block moves into a floating `ApprovalTray` above the composer; `HowSmallsGotHere` card (folds the working once a report exists); answer card rows rewritten to v4 order; `SidePanel` with three toggles (Working picture, Contacts, Ticket), push at ≥1100px, overlay below; one-line ticket turn with the note clamped to three lines; plain labels with "Details" for tool, params, query |
| ChatNewV4 (new chat) | `chat/NewChat.tsx` on `/chats/new` | `/chats/new` | `startChat` action, `Toggle` | Centred hero composer, attach button (disabled, see 6.6), "Not linked to an incident ▾", "Auto-approve checks", sidebar hidden with a top-left toggle |
| SummaryV4 (resolution) | `summary/SummaryScreen.tsx`, `lib/writeup/render.ts` | `/chats/[id]/resolution` (old `/summary` redirects) | Evidence rail logic (keep/clear, a signal drops when none of its evidence is kept), save, regenerate, edit note, `render.ts` | "In short" block (five rows), Resolution notes open, four collapsible sections each with a one-line summary, warning on the Evidence line when a signal drops, sticky action bar |
| MessagesV4 (communications) | `messages/MessagesScreen.tsx`, `Checks.tsx`, `KbView.tsx`, `lib/writeup/messages.ts` | `/chats/[id]/communications` (old `/messages` redirects) | Drafter, checks, copy-by-hash, autosave, Restore draft, KbView | Audience tabs across the top with state and check score, checks collapsed to failing ones, Copy at the bottom of the card, banner counting copies |
| TicketV4 | `Ticket` inside `ChatScreen.tsx` (fields only) | drawer on overview, panel on diagnosis | the field list | work notes newest first; Opened shown in UTC with Eastern beside it (the only Eastern on any screen) |
| ResolveDialog | `chat/ResolveDialog.tsx` "Mark it resolved" | dialog on diagnosis | radios, time, resolved-by, Escape | Title "Resolve incident", Resolution code select, "Resolution notes", "Continue to resolution review", the "Smalls' diagnosis" box |

Pages after the change: `/` (the first-run screen when nothing is imported, else the overview of the open incident, with the sidebar), `/chats/new`, `/chats/[id]`, `/chats/[id]/resolution`, `/chats/[id]/communications`, `/design` (kept, extended with the new pieces). Each `page.tsx` fetches and passes props; no client logic.

## 3. Global pieces

- **Layout.** `app/layout.tsx` renders a two-column shell: the sidebar island and the page. Below 820px the sidebar stacks above the page, as the design does. The diagnosis page is a 100vh column with a scrolling conversation and a sticky dock.
- **Header.** `IncidentHeader` takes the incident card: priority pill (from `priority_code`), red "Major incident" badge when the ticket marks it major (6.3), title, state tag from the ticket's `state` (no longer "Investigating"/"Resolved" invented by the UI; "Resolved" once resolved here). Meta line: number · business service · impact start (6.4) · View ticket. Diagnosis adds "next update due".
- **Status banner.** One component, four tones (action, attention, neutral, ok), one sentence plus optional button. Each screen passes its sentence; the wording comes from the design's `renderVals`.
- **Times.** Everything stays UTC through `lib/format.ts`. The ticket view adds Eastern beside Opened and on each work note, using the ticket source's zone from `integration.yaml` (served to the UI by the new ticket endpoint, never hard-coded).
- **Tokens.** `design/v4/ds/tokens.css` is diffed against `web/styles/tokens.css`; any new token (none expected beyond `--report-measure`) is copied. Screen CSS goes into `web/styles/screens.css` under new scopes (`.v-shell`, `.v-overview`, `.v-diag`, `.v-res`, `.v-comms`), replacing the old ones as each screen lands.
- **Confidence label.** New pure function in `lib/format.ts`: `confidenceLabel(0.78)` gives `"High (78%)"`. High is ≥ 0.7, Medium ≥ 0.4, otherwise Low. Used everywhere a confidence is written as text; the bar component stays for the working picture.

## 4. Vocabulary

Applied to every user-facing string in `web/`, to the drafting prompt `smalls/api/prompts/messages.md`, to the new overview prompt, and to the code-rendered drafts and write-up (`lib/writeup/*`). Model-facing agent text is left alone (6.2).

| Old | New | Where it changes |
|---|---|---|
| incident commander / IC | Major Incident Manager | ChatScreen bubbles, StatusTag "Rejected by IC" → "Rejected by you", IncidentCard fact, ResolveDialog, Drawer, chats/new |
| owner | Assignment group | AnswerCard row, messages checks ("Names who is on it" stays as the check's sentence) |
| leading / Leading, not yet sure | Probable cause / "No probable cause yet" | AnswerCard, AgentPanel, WorkingPicture, StatusTag display word. The `leading` status value in the report schema stays: it is a contract, and only its display word changes |
| Do next / Don't | Recommended action / Avoid | AnswerCard |
| read / reads | check / checks | Gate titles and hints, tally, chats list, banners, toggle "Auto-approve checks", `waitingText`, reducer's "The last N reads found nothing" |
| Investigate / Summary / Messages | Diagnosis / Resolution / Communications | step names, page titles |
| Mark resolved / Mark it resolved | Resolve incident… / Resolve incident | button, dialog, banner |
| knowledge base write-up | knowledge article | SummaryScreen, KbView, ResolveDialog, AnswerCard strip |
| Executives / Support team / Engineering | Senior stakeholders / Service Desk / Resolver groups | `audiences()` names in `lib/writeup/messages.ts`; the audience keys (`exec`, `support`, `status`, `eng`) stay, so the API and stored rows do not change |
| Impact | Business impact | ticket's "Impact / Urgency" stays (it is the ServiceNow field name); report-derived impact rows become "Business impact" |
| 0.78 / Likely | Confidence: High (78%) | AnswerCard, overview panel, banner, ResolveDialog |

**Where the design's own copy clashes with the table**, we follow the table: the ticket turn's "incident lead" becomes "Major Incident Manager"; NewIncidentV4's "Smalls asks before each read" becomes "before each check"; the import dialog's "Smalls reads the ticket…" becomes "Smalls takes the ticket…". "Read-only" (the tray's "Read-only. Nothing runs until you choose.") is a different sense and stays. Plain labels for chat-searching checks say "Look in…" rather than "Read…", so the done-when search stays clean.

**Done-when search.** A script `web/scripts/check-vocab.mjs` (run in `pnpm lint`) greps the rendered strings in `web/components`, `web/app` and `web/lib` for `\bread(s)?\b`, `leading`, `owner`, `incident commander`, ignoring identifiers, the `leading` enum value in code paths and the "Read-only" phrase, and fails on a hit. That turns the criterion into a check that stays true.

## 5. Backend changes (kept small)

### 5.1 "How Smalls got here" summary

The brief prefers two new fields on the tool-call record, filled by the agent. That makes the model fill them, which means a new field in the call schema, a new entry in `smalls/tools/descriptions.yaml` (a blind file, so a new blind pass), and a change to what the agent writes on every call. The brief allows the fallback, and we take it:

- **`plain_label`: code, in the web app, deterministic.** `lib/chat/labels.ts` maps each tool and its arguments to a short phrase: `search_logs` with terms and sourcetype gives "Search {sourcetype or every log} for {terms}", `list_changes` gives "List changes {window}", `read_chat` gives "Look in {channel} for {text}", `query_metrics` gives "Chart {metric} {window}", and so on for the eleven tools, falling back to the model's `rationale.tests`. It is generic (it reads arguments, never values from this bundle), unit-tested per tool, and lives outside the guarded folders.
- **`outcome_summary`: one summarisation call at the end of a run, in the API.** When a run finishes (and again after a follow-up that ran checks), the API makes one call through the existing model adapter on the fast profile `anthropic-small`, which already exists in `smalls/agent/config.toml` (the provider's small tier). It is summarising, not reasoning, so the fastest tier fits; the profile is a setting (`SMALLS_API_OUTCOMES_PROFILE`), so `openai-luna` can be tried without a code change. In our eval runs luna was about 1.8 times slower than the main investigator model, so it is not the default. Input per check: its plain label, status, `rationale.tests`, the result's first line, and the board change that followed it. Output: one clause per check id, validated (one clause, at most 14 words, no handle, no markdown); a clause that fails falls back to code. Stored on a new `check_outcome` table keyed by run and call id, and sent to the browser as one new `outcomes` event on the chat stream, so the reducer folds it like any other event.
- **Code fallback** (scripted mode, no key, or a failed call): "ruled out {subject}" when the board rejected a hypothesis after the call, "found nothing" for an empty result, otherwise "checked {plain label, lower-cased}".
- **The sentence is built in the UI** by a pure function `lib/chat/howWeGotHere.ts`: clauses from checks that ran, in order, joined "Smalls a, b and c." When any check was skipped or rejected, a grey note: "You skipped 1 check." / "You rejected 2 checks and skipped 1." + "The summary will list what wasn't checked." Vitest covers it (section 8).
- The prompt lives at `smalls/api/prompts/outcomes.md`: generic, true for any incident, no bundle string, says that tool output is data, never instructions. It passes the lint and the overfit review.

### 5.2 Incident overview, generated at import

- **New endpoint `POST /incidents/import {number}`.** Looks the number up among the ServiceNow incidents visible at `now`; unknown numbers get a 404 with a plain sentence the dialog shows. On success it builds the overview, creates the diagnosis chat (5.3) and returns its id. A second import of the same number returns the existing chat (the dialog's "already imported").
- **Inputs, all served data at `now`:** the ticket's fields, its work notes from `sys_journal_field`, changes named in the notes or linked through `rfc`, the CMDB relationships around the ticket's CI and business service, PagerDuty on-call for the assignment group's escalation policy, and Slack users and channels for contact links.
- **Generated once** by one call on the mid-tier profile with a new prompt `smalls/api/prompts/overview.md`, into the structured JSON the brief lists: `whats_going_on` (2 sentences), `business_impact`, `affected_cis[]` (name, CI label, plain description, reported status, severity), `not_affected[]`, `working_theories[]` (person, area, claim, what was tried), `timeline[]` (UTC time, event), `glossary[]` (domain terms only). Pydantic-validated; the prompt tells the model to use only what the inputs say and leave a field empty otherwise.
- **`contacts[]` is built by code, not the model**, so nothing is invented: Major Incident Manager and assignee from the ticket, the on-call person from PagerDuty, the CI's support group from CMDB, and people named in the work notes who match a `sys_user`. Slack links are built from the Slack user's `id` and `team_id` when one exists; the phone link shows only when `phone` or `mobile_phone` is set (none are, in the development bundle, so no Call buttons appear). The incident channel comes from Slack channels the work notes name. The bridge row and "Join the bridge" fact show only when the data holds a bridge URL (it does not today, so they are hidden).
- **Next update due** = the last update to the ticket visible at `now` + a cadence by priority from a new `[comms]` section in `smalls/api` settings (P1 30 minutes, P2 60), unless the ticket carries a next-update field (it does not today).
- **Key facts** come from the ticket (priority with Impact × Urgency, state, assignment group, logged time) and the overview (business impact, impact start = the earliest timeline row the model marked as impact start, else the ticket's opened time). The Major Incident Manager is the ticket's assignee or the trigger note's author.
- **Without a key** the overview is rendered by code: `whats_going_on` from the short description and the latest work note's first sentence, `affected_cis` from the CMDB neighbours with no status, theories, glossary and timeline left empty (and their sections hidden). Stored on a new `overview` table keyed by incident number. Not served to the agent (as U11 does for the knowledge base).
- **Nothing is imported at start.** Today the dashboard derives the incident from the bundle and labels it "Pushed from ServiceNow". After the change, the app opens on the first-run screen (EmptyV4) until someone imports, so no paid run starts on its own.
- **`GET /incidents/open`** lists the open P1 and P2 ServiceNow incidents visible at `now`, each marked imported or not. The first-run screen shows the ones not yet in Smalls under "Open in ServiceNow for your groups"; the import dialog shows the same list. Because the list is read from the data on every page load, a new P1 or P2 does appear there without anyone importing it, which is what the screen's line "New P1 and P2 incidents for your groups will appear here automatically" promises. The "✓ ServiceNow connected" line shows only when the ServiceNow source is loaded.

### 5.3 Auto-start

`POST /incidents/import` creates a chat with origin `headless` (the current "Run agent" path) and starts it. Its first run keeps today's unattended policy: its own checks run without asking and an action card is deferred. One change in `smalls/api/service.py`: today a headless chat forces `auto_approve` on for every run, including follow-ups; after the change the force applies to the first run only, and the chat's own switch (default off) governs follow-ups. That is the API's policy, not the agent's, and `tests/api/test_runs.py` gains a case for it.

### 5.4 Resolution code

`Resolution` in `smalls/api/models.py` gains `code: Literal["Solved (Work Around)", "Solved (Permanently)", "Solved Remotely (Work Around)", "Not Solved (Not Reproducible)"]`, default "Solved (Work Around)"; the `resolution` table gains a column; `resolveChat` and the dialog send it; `render.ts` shows it in "In short" and the knowledge article. `make api-types` regenerates the OpenAPI and TS types.

### 5.5 Ticket endpoint

`GET /incident/ticket?number=` returns the ticket's fields, its work notes (newest first, UTC, with the source zone's local time) and the source zone's name. The ticket view is the only consumer of the local time.

### 5.6 UI state

Sidebar visibility, the side panel's tab, collapsed sections and the tray's position are React state in the client islands. Nothing is persisted.

## 6. Judgment calls

1. **Outcome summaries by an API call, not an agent field** (5.1): the agent field forces a blind pass and changes what the agent writes on every call; the brief allows the fallback.
2. **Model-facing agent text keeps its words.** The blind prompts and the agent's mechanical strings ("the incident commander stopped the run", "REJECTED by…") say "incident commander", "leading" and "reads". Changing them changes what the agent reads, which the brief rules out. Where one of those strings reaches the screen (a stop notice, a failure line), the reducer maps it to the new words. The `leading` enum stays in the report contract.
3. **The "Major incident" badge** shows when the ticket's priority is 1 or a major-incident field is set. ServiceNow's `major_incident_state` is the authority; if absent, P1.
4. **Impact start** is the overview's earliest impact row, else the ticket's opened time, labelled as such ("Logged" sits beside it when both exist).
5. **"Earlier" in the sidebar** lists resolved chats from this session, saved knowledge articles and recorded runs. The design's "redis-sessions…" row and "Signed in as…" footer are sample data and are left out; the footer's dataset line is dropped, since the dataset name is a bundle string.
6. **The attach button** in the new-chat composer renders but is disabled with "Attaching files isn't available yet": the API has no upload, and adding one is outside "UI only".
7. **"Not linked to an incident ▾"** links the new chat to an imported incident (a select of imported incidents). Linking only sets the chat's `incident`; it does not change the run.
8. **Scripted mode reaches a probable cause.** Today the scripted run is always inconclusive, so the v4 answer card, the High banner and the resolve flow on the agent path never appear without a key or in Playwright. We add an option to `smalls/api/devscript.py` (`SMALLS_API_SCRIPT=concluded`) that marks its one generic hypothesis leading at 0.75 and proposes one generic action. It knows nothing about the data, as now, and e2e runs both scripts.
9. **The design's follow-up keyword router and canned answers are not built**: follow-ups keep going to the agent (U9).
10. **First-run screen copy.** The header's "Alice Nguyen · Commerce Platform" is sample data and there is no sign-in, so it is left out. The open-ticket row shows the real number, title and priority from the list endpoint; with no open P1 or P2, the row is hidden.
11. **The overview keeps the mid-tier profile.** It extracts theories, CIs and a glossary from raw notes, closer to drafting than summarising, so it uses `anthropic-mid` like the message drafter. Only the per-check outcomes use the fast tier.
12. **Audience keys stay** (`exec`, `support`, `status`, `eng`); only display names, channels and guides change.

## 7. Build order

One screen per commit, each with its tests, the vocabulary check for the files it touches, and a look at 1440px and 390px against the design file (screenshots through Playwright, saved under the scratch folder, not the repo).

| # | Commit | Includes | Acceptance |
|---|---|---|---|
| 0 | Plan and design | this file, `design/v4/` committed | reviewed |
| 1 | Shell | First-run screen, `GET /incidents/open`, Sidebar, New incident menu and Import dialog, IncidentHeader, StatusBanner, TicketView, layout change, `POST /incidents/import`, `GET /incident/ticket`, auto-start (5.3), confidence label, vocab check script | with nothing imported `/` shows the first-run screen with no sidebar; import a known number, or pick an open ticket → lands on overview with the sidebar; unknown number → message; steps disabled "after resolving" until resolved; ticket drawer shows notes in UTC with Eastern beside; boundary and type checks pass |
| 2 | Diagnosis | ApprovalTray, HowSmallsGotHere, answer card v4, SidePanel and toggles, ticket turn, plain labels, `outcomes` event and `check_outcome` table, the outcomes prompt, scripted "concluded" option | no panel open shows only conversation, banner, answer, composer; A/E/S/R/⇧A/⇧S work from the floating tray; existing gate tests pass with new wording; overfit review clean |
| 3 | Overview | overview generation (prompt, schema, code fallback, table), the overview components, diagnosis panel, contacts | overview renders from generated JSON and from the code fallback; empty fields hide; panel moves running → result |
| 4 | New chat | centred composer, sidebar hidden with toggle, link select, auto-approve toggle | after send it becomes the diagnosis layout |
| 5 | Resolution | resolution code (model, table, dialog, render), "In short", collapsibles, sticky bar | resolve with a code → review → save as knowledge article |
| 6 | Communications | tabs, collapsed checks, Copy at bottom, banner count, renamed audiences, drafting prompt wording | copy marks the audience; banner counts |
| 7 | Sweep | whole-flow e2e, vocab check across `web/`, HANDOFF and ui-build record updated | done-when list below |

## 8. Tests

- **Kept passing:** `make test`, `make lint`, `make web-test`. Tests that pin old wording ("Mark resolved…", "Do next", "Owner", "Executives", "◆ 2 reads need your approval", "Auto-approve reads", step names) are updated to the new wording in the same commit as the screen that changes it, with no change to what they check.
- **New Vitest:** `howWeGotHere.test.ts` (no checks, one, three; skipped and rejected notes; singular and plural; order kept; skipped checks contribute no clause), `confidence.test.ts` (0, 0.39, 0.4, 0.69, 0.7, 0.78, 1 and the percentage rounding), `labels.test.ts` (one case per tool and the fallback).
- **New pytest:** import (known, unknown, repeat), overview schema and code fallback, outcome clause validation and fallback, the follow-up auto-approve change, resolution code round-trip, ticket endpoint times.
- **New e2e:** `flow.spec.ts` rewritten to the brief's end-to-end path: import → auto diagnosis → overview shows the result → open diagnosis → follow-up → approve from the floating tray → Resolve incident with a code → resolution review → save as knowledge article → communications → copy. Plus a phone-width pass on every screen asserting no horizontal scroll (`scrollWidth <= innerWidth`), and axe in both themes as today.

## 9. Questions, answered 2026-10-20

1. **Import at start: no.** The app opens on the first-run screen (EmptyV4, added to `design/v4/`) until an incident is imported. Import from service desk is the recommended route; Start a chat is for when there is no ticket. Once one incident exists, `/` opens on the overview with the sidebar (5.2).
2. **Per-check outcomes: one model call after each run, on the fast tier** (`anthropic-small`), outside the agent (5.1).
3. **Agent prompts: unchanged.** The screens translate the agent's words where they show (6.2).

## 10. Done when

- Every screen matches its design file in structure, wording and hierarchy at 1440px and reflows at phone width without horizontal scroll.
- The vocabulary check passes: no user-facing "read", "leading", "owner" or "incident commander".
- The diagnosis screen with no panel open shows only the conversation, the banner, the answer and the composer.
- The end-to-end flow in section 8 passes in Playwright.
- Existing tests pass; the new summary-sentence and confidence-label tests pass.
- HANDOFF and `docs/decisions/ui-build.md` record what changed and why.
