# Smalls. Data-phase and ingestion targets are real; the others are stubs until their step in docs/PLAN.md.
UV ?= uv
PY := $(UV) run --python 3.12 python
BUNDLE ?= bundles/larkspur-checkout-2026-10-13
# The replay clock for the targets that need one: the second checkpoint, and the trigger file of the same name.
NOW ?= 14:52
CHECKPOINT ?= 1452
# Runs per cell of `make matrix`.
REPS ?= 5
# Keys come from .env when there is one; nothing here names or prints one.
ENV := $(if $(wildcard .env),--env-file .env)
# The commit a run record carries. A container has no git, so the host passes it in.
export SMALLS_COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null)

.PHONY: data lint ingest tag docker-test leak-test tools-smoke retrieval solve score demo run resume regress matrix heldout test schemas

data:
	$(PY) -m smalls_data.build

lint:
	$(PY) -m smalls_data.lint

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

score:
	@echo "score: stub until step 7 (scorer)"

demo:
	@echo "demo: stub until a first complete run is committed (build row C3)"

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
