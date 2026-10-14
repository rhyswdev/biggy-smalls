# Smalls. Every target is real but `heldout`, which waits for its step in docs/PLAN.md.
UV ?= uv
PY := $(UV) run --python 3.12 python
BUNDLE ?= bundles/larkspur-checkout-2026-10-13
# The replay clock for the targets that need one: the second checkpoint, and the trigger file of the same name.
NOW ?= 14:52
CHECKPOINT ?= 1452
# The committed run that `make demo` replays: the first complete live run, at the second checkpoint (build row C3).
DEMO_RUN ?= 20261014T090933Z-5d5522
# Runs per cell of `make matrix`.
REPS ?= 5
# Keys come from .env when there is one; nothing here names or prints one.
ENV := $(if $(wildcard .env),--env-file .env)
# The commit a run record carries. A container has no git, so the host passes it in.
export SMALLS_COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null)

.PHONY: data lint lint-every-bundle ingest tag docker-test leak-test tools-smoke retrieval solve score demo run resume regress matrix heldout test schemas

data:
	$(PY) -m smalls_data.build

lint:
	$(PY) -m smalls_data.lint

# Invariants 16 and 17 against the strings of every facts.yaml found, held-out bundles' included (PLAN 12's
# controls). Run by the session that authors a held-out bundle; an agent session runs `make lint` only.
lint-every-bundle:
	$(PY) -m smalls_data.prompt_lint --every-bundle

test:
	$(UV) run --python 3.12 pytest -q

# Build the store from the bundle and print the ingest report. No key, no network.
ingest:
	$(UV) run --python 3.12 smalls ingest --bundle $(BUNDLE)

# The only target that calls the classifier; needs JEV_API_KEY (from .env when present).
tag:
	$(UV) run --python 3.12 --group tagging $(if $(wildcard .env),--env-file .env) smalls tag --bundle $(BUNDLE)

# The "only Docker installed" path: the tests in the container, then a check that the image holds no secret.
docker-test:
	docker compose build smalls
	docker compose run --rm smalls make test
	docker run --rm smalls:dev sh -c '! ls -A /app | grep -q "^\.env" && ! env | grep -Eqi "(API_KEY|TOKEN|SECRET|PASSWORD)="'
	@echo "docker-test: tests pass in the container; the image holds no .env and no key"

schemas:
	bash schemas/fetch.sh
	$(PY) schemas/extract.py

# Both halves of the leak test, at 14:40 and 14:52: the store, then the output of every tool that is built.
leak-test:
	$(UV) run --python 3.12 pytest -q tests/test_leak.py tests/test_leak_tools.py

# Call every built tool once at NOW and print one line per tool. No key, no network.
tools-smoke:
	$(UV) run --python 3.12 smalls tools smoke --bundle $(BUNDLE) --now $(NOW)

# The retrieval check for search_knowledge: recall@1 and mean reciprocal rank over a dozen questions. A sanity check.
retrieval:
	$(PY) -m eval.retrieval.run --bundle $(BUNDLE) --now $(NOW)

# The scripted solve path: about fifteen tool calls from the trigger to the cause, each checked. No key, no network.
solve:
	$(PY) -m eval.solve.run --bundle $(BUNDLE)

# Score one run (RUN=<id>): scores.json in its folder. The judge is live and spends from eval/scorer.toml's budget;
# JUDGE=off scores without it.
score:
	$(UV) run --python 3.12 $(ENV) python -m eval.score $(RUN) $(if $(filter off,$(JUDGE)),--no-judge)

# Replay a committed run in the terminal, as it was shown live. No key and no model call.
demo:
	$(UV) run --python 3.12 smalls replay $(DEMO_RUN)

# One investigation at CHECKPOINT, in the terminal. Live: it needs a model key and it costs money.
run:
	$(UV) run --python 3.12 $(ENV) smalls run --bundle $(BUNDLE) --now $(NOW) --trigger-file $(BUNDLE)/triggers/$(CHECKPOINT).md --checkpoint $(CHECKPOINT)

# Go on with a paused run: make resume RUN=<run id>
resume:
	$(UV) run --python 3.12 $(ENV) smalls resume $(RUN) --bundle $(BUNDLE)

# The regression set: the agent at the second checkpoint, three runs, one process each. Live: it costs money for each run.
regress:
	$(UV) run --python 3.12 $(ENV) python -m eval.matrix --bundle $(BUNDLE)

# The matrix: the no-tools baseline and the agent, at both checkpoints, REPS runs per cell. Live, and REPS times dearer.
matrix:
	$(UV) run --python 3.12 $(ENV) python -m eval.matrix --bundle $(BUNDLE) --arms A,C --checkpoints 1440,1452 --reps $(REPS)

heldout:
	@echo "heldout: stub until step 11"

# -- The UI step (docs/decisions/ui-build.md) ----------------------------------------------------------
# The ref the overfit review diffs from: make overfit-review BASE=<ref>. The working tree is what is reviewed.
BASE ?= HEAD
# The ports the app listens on, away from the usual development defaults. The web app reaches the API at
# SMALLS_API_URL, which follows the API's port unless set.
SMALLS_WEB_PORT ?= 7317
SMALLS_API_PORT ?= 7318
SMALLS_API_URL ?= http://127.0.0.1:$(SMALLS_API_PORT)
# The API listens on the host's loopback only; compose sets 0.0.0.0, where only the compose network reaches it.
SMALLS_API_HOST ?= 127.0.0.1
# live runs the configured profile and needs a key; scripted plays the generic dev script, for development only.
SMALLS_API_MODE ?= live
API_ENV = SMALLS_BUNDLE=$(BUNDLE) SMALLS_CHECKPOINT=$(CHECKPOINT) SMALLS_API_MODE=$(SMALLS_API_MODE)

.PHONY: overfit-review api api-types api-fixtures app web-deps web-test

# The overfit review (ruling U15): every change under the agent, its tools, its contracts and the drafting
# prompt since BASE, read against the answer. Live: one model call with the investigator's profile.
overfit-review:
	$(UV) run --python 3.12 $(ENV) python -m smalls_data.overfit --bundle $(BUNDLE) --base $(BASE)

# The API alone, on 127.0.0.1:SMALLS_API_PORT. Live mode reads the key from .env; nothing is written to disk.
api:
	$(API_ENV) $(UV) run --python 3.12 $(ENV) uvicorn smalls.api.app:app --host $(SMALLS_API_HOST) --port $(SMALLS_API_PORT) --timeout-graceful-shutdown 3

# The web app's API types: the API's OpenAPI document from the app object (no server needed), then the
# TypeScript generated from it. A test fails when the committed document differs from the app's.
api-types:
	$(PY) -m smalls.api.openapi --write
	pnpm --dir web types

# The event logs the web app's reducer tests fold: scripted runs through the API over the made-up fixture incident.
api-fixtures:
	$(PY) tests/api/make_fixtures.py

# The app on the host: the API and the web app together, on http://localhost:SMALLS_WEB_PORT. Both ports are
# checked first, so nothing starts when one is taken. Ctrl-C stops both. The key stays with the API.
app: web-deps
	@$(PY) -m smalls_data.ports SMALLS_WEB_PORT=$(SMALLS_WEB_PORT) SMALLS_API_PORT=$(SMALLS_API_PORT)
	@echo "Smalls: http://localhost:$(SMALLS_WEB_PORT) (API on 127.0.0.1:$(SMALLS_API_PORT), $(SMALLS_API_MODE) mode)"
	@trap 'kill 0' INT TERM; \
	$(MAKE) --no-print-directory api & \
	SMALLS_API_URL=$(SMALLS_API_URL) SMALLS_WEB_PORT=$(SMALLS_WEB_PORT) pnpm --dir web dev & \
	wait

# The web app's packages, installed once from the lock file.
web-deps:
	@test -d web/node_modules || pnpm --dir web install --frozen-lockfile

# The web app's checks: the boundary rules, types and lint, the unit tests, then the browser suites (which start
# their own API in scripted mode and a web server, on ports of their own). Needs `pnpm --dir web exec playwright
# install chromium` once.
web-test: web-deps
	pnpm --dir web lint
	pnpm --dir web test
	pnpm --dir web e2e
