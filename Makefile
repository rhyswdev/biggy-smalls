# Smalls. Data-phase and ingestion targets are real; the others are stubs until their step in docs/PLAN.md.
UV ?= uv
PY := $(UV) run --python 3.12 python
BUNDLE ?= bundles/larkspur-checkout-2026-10-13

.PHONY: data lint ingest tag docker-test leak-test tools-smoke score demo run heldout test schemas

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

# The store half of the leak test, at 14:40 and 14:52. The tool-output half joins it with the tools.
leak-test:
	$(UV) run --python 3.12 pytest -q tests/test_leak.py

tools-smoke:
	@echo "tools-smoke: stub until step 6 (tool layer)"

score:
	@echo "score: stub until step 7 (scorer)"

demo:
	@echo "demo: stub until step 10"

run:
	@echo "run: stub until the agent exists"

heldout:
	@echo "heldout: stub until step 11"
