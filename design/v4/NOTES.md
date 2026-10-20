# Smalls v4 design notes

Exported from the design canvas. Each .dc.html file is one screen: markup in <x-dc>, logic in the script block at the bottom (renderVals returns the data each screen shows).

Incident overview v4 (see the ITIL note below for terms)

Written for an incident manager who has just joined the call and doesn’t know payments.

• What’s going on: two sentences in plain words, then four facts.
• What’s affected: the path a payment takes, each part with a plain one-liner and what’s been reported there. The ticket’s CI and service fields become this.
• Who thinks what: the theories from the work notes, by person, with what’s already been tried.
• Payments terms you’ll hear: only the domain-specific ones (PSP, authorisation, idle in transaction). Incident managers already know 504s, rollbacks and load balancers.
• Key contacts with Slack and call links (fake), plus the incident channel and bridge.
• Smalls starts on its own when the ticket is imported, and its answer lands in the right-hand panel and the banner.
• View ticket opens the raw ServiceNow view in a drawer, for anyone who needs it.

---

Investigation v4

• Opens on the agent’s finished run. The ticket card becomes one line saying how it started, Alice’s note clamped, and View ticket.
• The answer leads in plain words: what’s wrong, do next, owner, how sure, don’t. The technical cause and evidence are one click away.
• The first five checks ran on their own. Follow-up checks still ask for approval in the tray above the composer, with the same keys.
• Supporting info is off by default. Working picture, Contacts and Ticket are toggle buttons at the top right (with counts). One opens a side panel that pushes the conversation over; click again or × to close. Under 1100px wide it slides over instead. View ticket opens the same panel on Ticket. Try the panel tweak to preview each.
• New incident (top of the sidebar, every screen): Start a chat, or Import from service desk. Start a chat is 2b, which asks before every read.

---

Summary and Messages v4

• Same as v3 without the lenses. ‘In short’ uses plain words for what went wrong, and all detail sections start closed.

---

Start a chat v4 (2b)

• Immersive, like starting a chat in Claude: the menu hides itself (sidebar button top left brings it back), no header, one centred question.
• The composer sits in the middle with attach and Send inside it, and two quiet controls underneath: link to an incident, and auto-approve.
• Once you send, it becomes the normal investigation: header, banner, approvals tray and composer docked at the bottom.

---

ITIL / ITSM pass (v4)

Vocabulary
• Major Incident Manager (was incident lead / IC), Assignment group (was owner), Recommended action / Avoid, Probable cause (root cause is for Problem Management), Business impact, Affected service and CIs, Third-party supplier.
• State: In Progress plus a Major incident badge; P1 · Critical (Impact 1 × Urgency 1); Impact start vs Logged.
• Steps: Overview, Diagnosis, Resolution, Communications. Audiences: Senior stakeholders, Service Desk, Status page, Resolver groups.
• Resolve incident with a resolution code and resolution notes. Knowledge article, not write-up.

Concepts added (presentation of data the app already has)
• Key facts strip on the overview: priority, state, MIM, assignment group, business impact, impact start, next update due, bridge.
• Workaround vs permanent fix: resolution code Solved (Work Around); follow-up says raise a Problem for PAY-2231.

Consistency
• One word, checks (was reads and checks). One confidence form: High (78%). The cause is stated once in the banner, then the answer card adds why, action, group and confidence. UTC only, Eastern stays in the raw ticket. Timeline and payments terms fold away on the overview.
