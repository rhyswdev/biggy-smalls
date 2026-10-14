# A live incident, as of 14:52 UTC

You are an experienced SRE joining an incident call. It is 14:52 UTC on 2026-10-13. Find what is causing the incident, using only the tools in this folder, and say how sure you are and what to do.

This is a test of whether the cause can be found from the evidence at all. Nobody has given you the answer, and nothing here contains it except the evidence itself. Work as you would on a real call: form theories, test them against the records, and drop the ones the records do not support.

## The incident commander's message

From: Alice Nguyen (incident commander)
Time: 14:52 UTC

We rolled checkout-api back to 2.14.2 (done 14:42) and nothing changed. Still 504s on submit, error rate about 17%, and support has 40+ tickets about the payment spinner.

Elena has been in the database and posted what she found in the incident channel. Sam thinks pay-03 is dying, it keeps dropping out of the F5 pool. Kofi thinks it's the network because the marketing site is down too, and he says PayCore's status page is green.

I need: what is actually causing this, how sure you are, what we should do in the next 10 minutes, and who should own it.

## How to read the evidence

Only through `./tools`, which answers as of 14:52; nothing later exists yet.

- `./tools list`: the eleven tools.
- `./tools schema`: what each tool does and its arguments. Read it first.
- `./tools call <tool> key=value ...`: one call, for example `./tools call list_alerts since=-2h`. A value is read as JSON when it parses as JSON, so a list is `terms='["timeout"]'`.

Every row a tool prints starts with a handle (such as `sw:54977`). `./tools call get_record handle=<handle>` prints the whole record. Cite handles for everything you claim.

## Rules

- Read the evidence only through `./tools`. Do not open, list or search the files under `bundle/`: they hold records from after 14:52, which the tools hide, and reading them ends the test.
- Do not read, list or search anything outside this folder, and do not use the network.
- Do the work yourself, in this conversation, without starting other agents.
- Aim to finish in about 20 minutes of work. Stop at 45 minutes whatever you have, and write it down. Not finding the cause is a valid result; say so plainly.

## Write FINDINGS.md in this folder

Note the time when you start, and write the file as you go.

1. **The cause.** What is failing and why, in a few sentences, and your confidence from 0 to 1. Keep apart what is broken, what set it off, and what made it worse, if you can tell.
2. **The evidence.** For each link in that account, the handles that show it, and what each shows.
3. **The other theories.** Each theory in the commander's message, and any other you weighed, with whether the records support it and the handles that decide it.
4. **What to do.** In the next 10 minutes, what not to do, and who should own it, with how you know who owns it.
5. **How it went.** When you started and finished, how many calls you made (`calls.log` has them), where you were stuck or misled, what a tool did not let you do, and anything you think the evidence should have shown and did not.
