# Smalls. Data-phase targets are real; the others are stubs until their step in docs/PLAN.md.
UV ?= uv
PY := $(UV) run --python 3.12 python

.PHONY: data lint leak-test tools-smoke score demo run heldout test schemas

data:
	$(PY) -m smalls_data.build

lint:
	$(PY) -m smalls_data.lint

test:
	$(UV) run --python 3.12 pytest -q

schemas:
	bash schemas/fetch.sh
	$(PY) schemas/extract.py

leak-test:
	@echo "leak-test: stub until step 6 (tool layer)"

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
