# Tagging at ingestion: the research behind the decision

Research doc, 2026-10-13. It records what we checked about jev, the classifier used by the tagging pass in step 6a, so a reader can verify it. What was decided (the rules, the taxonomy, the thresholds, the cache, the open points) is in `docs/decisions/ingestion-decisions.md`, section 4; where the two differ, that record wins. This doc was written without reading any held-out or answer-bearing path.

Sources: the vendor docs at docs.typesafe.ai, the public OpenAPI spec, PyPI metadata, the vendor's public agent skill file, and five live API calls made with synthetic text written for the check. Claim tags:

- **[docs]** stated in the vendor's docs; the URL is given.
- **[live]** observed in a live call or a local SDK run on 2026-10-13.
- **[inferred]** our reading; stated nowhere.

By house rule this doc names no classifier version. "The pinned classifier version" means the versioned id held in config.

## 1. What jev is, and what it is not

Jev is a hosted model from TypeSafe, reached through one HTTP endpoint (`POST https://api.typesafe.ai/v1/systemone`) with official Python and JavaScript SDKs. It is the vendor's own model, not a library over other providers: the same weights serve every account, with no per-customer fine-tuning. [docs: https://docs.typesafe.ai/introduction, https://docs.typesafe.ai/models]

It does not generate text. A call sends a `state` (a string, a JSON object or an array) and a map of typed `questions`, and returns one typed answer per question. [docs: https://docs.typesafe.ai/primitives]

| Question type | Asks | Returns |
|---|---|---|
| `Choice` | one option from a closed set, at most 255 options | `choice`, a probability for every option, `confidence` |
| `Noul` | yes or no | `noul`, the probability of yes from 0 to 1; no separate confidence |
| `Score` | a position on 2 to 10 ordered levels | `score` (probability-weighted, can fall between levels), `legend`, probabilities, `confidence` |

What it is not: "It does not generate text, write code, or hold a conversation." [docs: https://docs.typesafe.ai/introduction/coding-agents] So there are no free-text outputs, no rationales, no extraction by generation, and no nested or array outputs: the response is a flat map of question id to answer. A closed taxonomy maps onto it directly; anything open-ended does not. There are also no stored or named classifiers on the vendor's side: the "classifier" is the questions we send each time, so versioning it is ours to do. [inferred from the API surface]

## 2. The cookbooks, and why the Twitch chat filter is the basis

The vendor publishes twenty cookbooks at https://docs.typesafe.ai/cookbooks. Each URL below is `https://docs.typesafe.ai` plus the path; appending `.md` gives raw markdown. These were the candidates for our problem.

| Cookbook | Path | What it demonstrates | What it would have been for here |
|---|---|---|---|
| Twitch chat filter | `/cookbooks/twitch_chat_filter` | One request per short chat message: a 5-way `Choice` category plus two `Noul` flags; eight requests at once; decision rules in code; a disk cache | **The basis.** Closed-set labels on short, independent, mixed records |
| Guardrails for LLMs | `/cookbooks/llm_guardrails` | One request per message with a battery of `Noul` questions plus a severity `Score`; thresholds in code | Multi-label facets through yes/no questions, one `Noul` per label |
| Pre-parsed value extraction | `/cookbooks/pre_parsed_value_extraction_cookbook` | Code finds candidate spans; a `Choice` over those spans picks one, so the value is a verbatim copy | Choosing among candidate entity mentions found by code |
| Knowledge graph entity alignment | `/cookbooks/entity_alignment` | One `Score` plus three `Noul`s decide whether two catalogue records are the same product | Deciding whether two names from different sources are one entity |
| Re-ranking | `/cookbooks/rerank_typesafe` | One `Noul` per query and candidate pair re-ranks a BM25 shortlist | Re-ranking `search_knowledge` hits |
| Classification using confidence | `/cookbooks/classification_using_confidence` | One `Choice` over 75 groups; falls back to a broader class when confidence is low | A fallback up a tree, which our small facets do not have |
| Hierarchical classification | `/cookbooks/hierarchical_classification` | Deep taxonomies by beam search over chained `Choice` probabilities | Hundreds of leaves, which we do not have |
| Parallel questions | `/cookbooks/parallel_questions` | 13 questions in one call against 13 calls: 12.2 times cheaper, 10.0 times faster, same answers | Asking every facet of a record in one request |
| Self-consistency: choices | `/cookbooks/consistency_choice_cookbook` | Run-to-run label agreement; adds an `uncertain` outcome below a top-probability floor | The floor under which a facet stores nothing |

**Why the Twitch chat filter.** It is structurally our problem. The unit of work is the same (one short record, one request). The answer shape is the same (a category from a closed set). The split of duties is the same: the model returns probabilities and a function in code turns them into labels, so a rule change needs no new request. It already shows the three properties the pass needs: a label set declared in code, a disk cache so a second run makes no calls, and bounded concurrency with order kept. It is also the simplest of the classification cookbooks. [docs: https://docs.typesafe.ai/cookbooks/twitch_chat_filter]

**What the others would have been for.**

- *Multi-label.* A `Choice` picks exactly one option; multi-label is not a native answer type. The documented route is one `Noul` per label, thresholded in code: "use one per label when several may apply". [docs: https://github.com/typesafe-ai/skills/blob/main/skills/typesafe-ai/SKILL.md] The guardrails cookbook is the worked version.
- *Spans.* Jev cannot emit a span; it can only choose among spans we hand it. The cookbook's own caveat: "Finding the candidates is the part that takes work." Names have no regex, "so its candidates have to come from a roster you already have". [docs: https://docs.typesafe.ai/cookbooks/pre_parsed_value_extraction_cookbook] For us, mention detection would stay in code against a roster built from structured fields, and the classifier would only disambiguate roles. [inferred]
- *Entity alignment and re-ranking.* Both are pairwise judgments. They belong to alias resolution and to knowledge search, which this step does not build.

**Where the cookbook does not fit as written.** It reads the key from the SDK's own variable and uses a placeholder key when unset; its cache key is the state plus the questions, "not the model"; it caches a reduced answer; it always applies the winning label; and its cache helper is a separate cookbook package. How we changed each of these is in the decision record, section 4.1.

## 3. The integration surface we rely on

**Package.** `typesafe-sdk` on PyPI, import name `typesafe_sdk`, MIT. Latest is 0.7.4 (2026-10-09); the first public release was 0.5.7 on 2026-09-14, with breaking changes in 0.6.0 and 0.7.0. It requires Python 3.10 or later and depends on `httpx2`, `pydantic>=2.12`, `pydantic-core`, `tenacity`, `truststore`, `packaging` and `typing-extensions`. It installed cleanly on Python 3.12.13, and the project lock already satisfies the pydantic pins. Sync and async clients both exist with the same surface. [live: PyPI metadata and a local install; docs: https://docs.typesafe.ai/sdk/python, https://docs.typesafe.ai/sdk/python/changelog]

**How the key is passed.** The SDK reads `TYPESAFE_API_KEY`, not our `JEV_API_KEY`, so the key is passed explicitly: `TypeSafeClient(api_key=os.environ["JEV_API_KEY"], model=<pinned version>)`. This worked on the first call. [live] HTTP auth is `Authorization: Bearer <key>`. A missing or malformed key raises `TypeSafeError` when the client is constructed, before any request, so a path that only replays a cache must build the client lazily or not at all. No project, classifier or dashboard object has to exist first. [docs: https://docs.typesafe.ai/sdk/python/usage#environment-variables]

Key safety: at `debug` log level the SDK redacts auth headers but logs request and response bodies unredacted. [docs: same page]

**How a label set is declared.** The label set is the keys of a `Choice`'s `criteria` map; each value is that label's description, or `None` for a name-only label. Question ids are for our code only and "are not sent to the model", so the whole meaning must be in `instructions`. Fields of a structured state are referenced in instructions with backticked paths. A question may also be a plain dict. [docs: https://docs.typesafe.ai/primitives, https://docs.typesafe.ai/introduction/quickstart] One question from our live request, with synthetic text:

```json
{
  "state": {"record_kind": "log_template", "source": "application log",
            "text": "ERROR <*> [http-nio-8080-exec-<*>] c.example.orders.OrderService - Failed to acquire connection from pool after <*> ms: java.sql.SQLTransientConnectionException: pool exhausted"},
  "model": "<pinned version>",
  "questions": {
    "category": {"type": "choice",
                 "instructions": "Which category best describes what `text` reports?",
                 "criteria": {"error": "A failure, exception, or rejected operation",
                              "warning": "A degraded or risky condition that has not failed yet",
                              "state_change": "A deploy, restart, configuration change, or scaling event",
                              "routine": "Normal operation, health checks, or successful requests",
                              "other": "None of the other categories fits"}}
  }
}
```

The call is `client.system_one(state, questions, model=None, retry=None, timeout=None, ...)`. [docs: https://docs.typesafe.ai/sdk/python/api/clients/sync]

**The response shape.** The answer to that question, from the raw HTTP response (the same request carried four more questions, left out here). [live]

```json
{
  "model": "<pinned version>",
  "answers": {
    "category": {"type": "choice", "choice": "error", "confidence": 1.0,
                 "probabilities": {"error": 1.0, "warning": 0.0, "state_change": 0.0, "routine": 0.0, "other": 0.0}}
  },
  "usage": {"input_tokens": 617, "output_tokens": 122}
}
```

- `choice` is always one of the option keys we supplied. Every `Choice` and `Score` returns the full distribution, rounded to two decimals in what we saw. `confidence` is derived from the distribution by a published formula. [docs: https://docs.typesafe.ai/confidence]
- A `Noul` answer is `{"type": "noul", "noul": 0.99}`. There are no explanations of any kind.
- `model` reports the versioned id that answered. `usage` carries `input_tokens` and `output_tokens`.
- The request id is the response header `x-typesafe-request-id`, exposed as `response.request_id`. No rate-limit headers were present on a 200. [live]
- The SDK object is `SystemOneResponse` with `.answers` and typed views `.choices`, `.nouls`, `.scores`; `response.model_dump(mode="json")` round-trips cleanly, which is what a cache needs. [live]

**Errors and retries.** 401 for a missing or invalid key, 422 for a body that fails validation (the body names the offending field; seen live for a `Choice` sent without `criteria`), 429 over a rate limit, 529 when the service is overloaded. [docs: https://docs.typesafe.ai/api#errors] The SDK retries by default: 2 retries on 408, 429 and 5xx, on connection errors and on timeouts, with backoff from 0.5 s to 5 s, respecting `Retry-After`, inside a 30 s budget per call; the per-operation HTTP timeout defaults to 10 s. [docs: https://docs.typesafe.ai/sdk/python/api/retries, https://docs.typesafe.ai/sdk/python/api/constants]

**Offline.** No official mock or test mode is documented. Two routes work: replaying a JSON cache with the client constructed on a placeholder key (the cookbook route), and passing `transport=` to the client, where `httpx2.MockTransport` served a canned response with no network call in a local run. [docs: cookbook; live]

## 4. Operational facts that shaped the design

| Topic | Fact | Source |
|---|---|---|
| Determinism | **Not deterministic.** Two byte-identical requests pinned to one version returned 0.09 and then 0.11 on one `Noul`; every other answer matched. The docs agree: "a little run-to-run sampling noise" on two of thirteen questions, and "This policy does not make the model deterministic." | [live]; [docs] https://docs.typesafe.ai/cookbooks/parallel_questions, https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook |
| Seed or temperature | No such request field. The request takes `state`, `model` and `questions` only. | [docs] https://docs.typesafe.ai/api; [live] https://api.typesafe.ai/openapi.json |
| Server-side result caching | Not documented. The differing answers above and `cf-cache-status: DYNAMIC` show identical requests are evaluated again. | [live] |
| Batch endpoint | None. The spec has two paths, `/v1/systemone` and `/v1/models`, and there is no batch discount. "Batch" means many questions in one request, or many requests at once from a thread pool. | [live] https://api.typesafe.ai/openapi.json |
| Pinning a version | Supported: send the versioned id instead of the alias "and move to the new one on your own schedule". Aliases move on release. | [docs] https://docs.typesafe.ai/models |
| Pricing | $42 per billion input tokens ($0.042 per million). Output tokens are free. | [docs] https://docs.typesafe.ai/models |
| Free tier, credits, spend caps | Not documented; the pricing page returned 404. | [live] https://typesafe.ai/pricing |
| Rate limits | 100K tokens per second and 80 requests per second; over either returns 429. "Rate limits are adjusting dynamically ... can change without notice." | [docs] https://docs.typesafe.ai/models |
| Latency | "Most queries complete in about 100 ms"; the cookbook's median is about 0.4 s per message; our five calls took 0.20 to 0.29 s round trip. | [docs] https://docs.typesafe.ai/concepts/how-to-build-with-system-one, https://docs.typesafe.ai/cookbooks/twitch_chat_filter; [live] |
| Context limits | 64k tokens per request (state plus all questions); 32k tokens for the state plus the longest single question. Text only. | [docs] https://docs.typesafe.ai/models |
| Language | English is primary; other languages are accepted with lower accuracy. | [docs] https://docs.typesafe.ai/models |
| Training on inputs | "Jev is not trained on customer requests or responses." The privacy policy: "We will not train or fine tune any artificial intelligence or machine learning models on your prompts or other Input." | [docs] https://docs.typesafe.ai/models, https://typesafe.ai/legal/privacy-policy |
| Retention | No period stated. The data processing terms say "retained for as long as necessary taking into account the purpose of the Processing." Zero data retention is offered to enterprise customers only. | [docs] https://typesafe.ai/legal/data-processing, https://docs.typesafe.ai/legal |

**Cost at our size.** Our small requests cost 408 input tokens with two questions and 617 with five, and the docs' one-`Noul` example costs 296, so there is a fixed overhead of roughly 250 tokens per request plus tens of tokens per question. [inferred from live usage] A few hundred requests of that size cost about a cent, far inside the documented limits. Cost and rate limits are not constraints here.

**The stated weaknesses.** The vendor publishes a failure-mode page for each version, under https://docs.typesafe.ai/model-jaggedness/ (the path ends in the version, which we do not write here). For the pinned version it says: [docs]

- "the order of a Choice's options can affect the answer", with a lean toward the option that comes first;
- it reads literally, and is weak at counting, numeric comparison and date arithmetic;
- it is weak on low-level or encoded text and on multi-hop instructions;
- accuracy falls as the state grows with detail the question does not need;
- the state is not treated as hostile: "Content written to adversarially steer the model ... can move the answer."

Two consequences. Facets should be semantic judgments, never something code can compute (times, severity fields, counts). And log templates with stack traces and hex-like tokens sit nearer its weak side than chat messages do: our one synthetic log template was classified sensibly, which is not evidence of accuracy.

**The SDK is young.** It has been public for one month, with two breaking releases, and is pre-1.0. It brings `httpx2`, not `httpx`. [live: PyPI]

## 5. Questions still open

1. **Version lifetime.** How long a pinned version stays served after a newer release is not documented, and there is no deprecation policy. The cookbooks cite two-part version ids while the models page lists a three-part id; whether both forms are supported is not stated.
2. **Billing state of the key.** Free credits, the plan and any spend cap are not documented and not visible through the API.
3. **Size of the noise on our taxonomy.** One of five answers moved by 0.02 in one repeat. We do not know the distribution; the stability check in the decision record (section 4.6) measures it on our labelled sample.
4. **Accuracy on our record kinds.** Nothing in the docs evaluates jev on logs, stack traces, alert payloads or change records. The precision measure in the decision record (section 4.6) is our only evidence.
5. **Maximum questions per request.** Only the token budgets are documented. The largest documented single request has 218 options in one `Choice`; another cookbook asks 62 questions in one request.
6. **Token accounting.** The fixed overhead per request and the cost per question are inferred from four data points, not documented.
7. **Retention.** How long request bodies are kept on a standard account is not stated. The bundle is synthetic, which is why this did not block the decision.
8. **How held-out bundles get tags.** Deferred: we settle it separately, outside this step. Tags are off by default, so held-out runs need none and nothing waits on it (decision record, O4). The other question raised here, whether a pinned version in config counts as a model identifier under the repo rule, is ruled: the rule means model-agnostic design, and a version held in config and referred to by role is that rule applied (O3).

## 6. What was run live

Five API calls on 2026-10-13, all with synthetic text written for the check. The key was loaded from the local `.env` into the process and never printed.

1. `GET /v1/models`: 200, 0.24 s.
2. `POST /v1/systemone`, five questions, pinned version: 200, 0.27 s.
3. The identical request again: 200, 0.24 s; one `noul` differed (0.09 against 0.11).
4. `POST /v1/systemone` with a `Choice` missing `criteria`: 422.
5. The SDK at 0.7.4 on Python 3.12.13, key passed explicitly, two questions: 200, 0.29 s.

Plus one offline SDK run through `httpx2.MockTransport`, with no network. The SDK was installed into a throwaway environment outside the repo; nothing was written to the repo.

## 7. What the tags give on the bundle, and candidate facets we tried

Written on 2026-10-13, after the first `make tag` run. The shipped taxonomy is unchanged by this section: it records what we measured and tried, so the next ruling has numbers. Every candidate was run through `smalls tag --experiment`, which writes beside its spec file under `eval/tags/experiments/` and never touches the committed cache. The profiles and candidates were written by a session that knows the development incident, so each candidate is tied to a vocabulary older than it, and the risk that remains is stated per candidate.

### 7.1 What the shipped tags are good for

**Counts over time by meaning.** With tags on, grouping the visible log records at 14:52 by `signal` in 5-minute buckets, each count multiplied by its `keep_1_in`:

| From (UTC) | latency | errors | saturation | routine | untagged |
|---|---|---|---|---|---|
| 14:00 | 0 | 2 | 0 | 35,807 | 51 |
| 14:20 | 1 | 4 | 0 | 37,006 | 53 |
| 14:25 | 710 | 201 | 0 | 35,998 | 4,342 |
| 14:30 | 205 | 1,605 | 248 | 38,246 | 15,232 |
| 14:35 | 0 | 7,103 | 2,272 | 41,144 | 24,968 |
| 14:45 | 4 | 15,106 | 4,908 | 47,006 | 21,717 |

`routine` stays flat while `latency` appears first, then `errors` and `saturation`: the order of events shows without the agent knowing one vendor string. This is what `search_logs group_by=signal` with a bucket returns.

**Other uses the contract already names**: one word across vendors (`signal=saturation` reaches log lines, alert definitions, alerts and tickets); the theories in a channel (`act=hypothesis`) apart from what has been done (`act=action_taken`); changes by kind.

**The weakness.** The untagged column is large, and it is the diagnostic lines. A pick-one question splits its probability when a text is two things at once: a failed request that is also an exhausted pool answered `saturation` 0.50 and `errors` 0.48; a 504 answered `errors` 0.64 and `availability` 0.35. Of the untagged log records, the top two answers were `availability` and `errors` for 319, `errors` and `saturation` for 111, `errors` and `latency` for 73. The floor does what it is for (no wrong tag), and the price is paid on exactly these lines.

### 7.2 Candidates

A second sample of 45 units, sharing none with the first, was labelled by hand before the candidates were run (`eval/tags/experiments/facets-1.labels.yaml`): 17 for `signal`-type candidates, 14 for `act`, and all 14 metric series. For a yes/no candidate the label is every option that applies. Figures are at the floor of 0.93 unless said.

| Candidate | What it is, and where its options come from | Blind sample: stored, right | Over the bundle | Repeat: stored values not reproduced |
|---|---|---|---|---|
| `act` as yes/no | The shipped `act` options, one yes/no question each | 12 stored on 9 of 14 units, 12 right (pick-one: 5 of 14 units) | 76 of 110 units, 16 with two or more values (pick-one: 37) | 4 of 97 |
| `signal` as yes/no | The shipped `signal` options, one yes/no question each | 8 stored on 5 of 17 units, 8 right (pick-one: 12 of 17 units) | 2,255 of 3,989 log records with the reworded option below (pick-one: 1,914); either form: 2,662 | 2 of 82 |
| `signal` on metric series | The shipped golden-signal options on a series' query | 13 of 14 series, 13 right | 13 of 14 | 0 of 13 |
| `resource` | Which finite resource: the USE method's list, software resources folded into one `pool` option | 6 of 17 units, 6 right | 35 of 165 units (pool 19, disk 6, cpu 5, memory 4, network 1); 610 log records | 0 of 35 |
| `fault` | How it failed: the classes of the canonical RPC status codes | 3 of 17 units, 3 right | 31 of 165 units (not_found 10, exhausted 9, timeout 6, unavailable 4); 676 log records | 0 of 31 |
| `layer` | Which tier a text is about; the facet the ruling named first to add back | 8 of 17 units, 8 right | 80 of 165 units (application 37, database 22, platform 17); 2,057 log records | 4 of 80 |

What we read from it:

- **`act` gains most from yes/no.** A message that reports and proposes gets both labels where pick-one gave neither. `status` and `social` almost never answer yes at the floor (1 and 0 units), so those two would stay pick-one's job or go unused.
- **`signal` as yes/no complements pick-one; it does not replace it.** It tags the two-things lines (a refused call as `errors` and `availability`, a pool timeout as `errors` and `saturation`), but it rarely says yes to `routine` (10 units against pick-one's many). The best coverage is the union of the two forms.
- **A wording defect the sample did not catch.** In the yes/no form the `security` option ("Authentication, authorisation, certificates, access or audit.") answered yes at up to 0.94 on lines about authorising a payment: the word, not the meaning; 13 units, 661 log records. Reworded to "Who is allowed to do what: logins, permissions, certificates, access control, audit trails.", it answers yes on 1 unit. In the shipped pick-one form the same collision only costs a tag (the line splits between `latency` and `security` and stays untagged), and the rewording there changes little (82 to 88 units). None of the 17 sampled units held such a line, which is the plain limit of a sample this size: 8 right of 8 did not see it. A yes/no facet should not ship on that sample alone.
- **Metric series tag cleanly.** A query string names what it measures (`...request.duration`, `...connections`, `...request.hits`). The one untagged series is a success rate. This would let the source inventory group series by golden signal.
- **`resource` and `fault` are precise and sparse**, as they should be: most lines are about no resource and report no failure. They would answer "which resource" and "how it failed" directly (`resource=disk`, `fault=timeout`). The risk is the one open point O1 already names: their busiest values here (`pool`, `exhausted`, `timeout`) are this incident's own mechanism, and although the option lists come from the USE method and the RPC status codes, we cannot show they would read the same to someone who had not seen this incident.
- **`layer` mostly restates the source**, as the ruling expected: application lines from application logs, database lines from the database log. It adds something only where a line is about another tier than the one that wrote it (a pool error in an application log tagged `database`).

### 7.3 What it cost, and what is not measured

Two experiment passes of 289 requests each over the same units (762,816 input tokens together, about 3 cents), on top of the 494 requests of the shipped run. The second pass repeated every unchanged question, which is where the "not reproduced" column comes from; no pick-one candidate changed value between passes. Not measured: stability under shuffled option order for the candidates; precision on more than 14 to 17 units per candidate; anything on a bundle other than the development one.

### 7.4 What we adopted, and what was left out

Ruled on 2026-10-13 (`docs/decisions/ingestion-decisions.md` section 4.7 and point O6). `act` is asked as yes/no per option, and `signal` covers metric series; both were re-run under their shipped profiles and scored on labels committed first. `signal` is asked both ways: we set its test in advance (at least 9 in 10 stored values matching on a larger sample labelled first), scored it from the answers of the second pass on 45 units, 28 of them newly labelled, where all 37 stored values matched, read every value it adds, and then ran it under its shipped profile. `resource`, `fault` and `layer` are left out for the reasons in 7.2. The floor rule gained a tolerance of 0.02 for the wobble section 4 describes, and the measured floor is 0.90 (`docs/DATA.md` 119 to 124).

One figure above needs its context now: the table in 7.2 compares each candidate with pick-one at the floor of the time, 0.93. At the final floor of 0.90, pick-one `act` would tag 43 of 110 units and the adopted yes/no form tags 86; pick-one `signal` tags 90 of 165 units and the two forms together 117.
