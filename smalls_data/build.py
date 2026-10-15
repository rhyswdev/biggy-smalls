"""Build every bundle from scenario/*.yaml. Entry point for `make data`.

The slice profile (PLAN 6, steps 3 to 5) renders the sources marked S in PLAN section 2. nginx, F5,
PagerDuty, SolarWinds, the live ServiceNow incident and its tables, PayCore v2/v3 and the knowledge
pages beyond the two runbooks arrive in steps 7, 9 and 13."""

from __future__ import annotations

import json
import shutil
import sys

from smalls_data.gen.checkout import Checkout
from pathlib import Path

from smalls_data.gen.core import BUNDLES, SCENARIO, content_path, load_ctx
from smalls_data.gen.datadog import Datadog
from smalls_data.gen.dynatrace import Dynatrace
from smalls_data.gen.edge import F5, Nginx, SolarWinds
from smalls_data.gen.jenkins import Jenkins
from smalls_data.gen.k8s import K8s
from smalls_data.gen.misc import Confluence, Integration, PayCore, Triggers
from smalls_data.gen.model import Model
from smalls_data.gen.pagerduty import PagerDuty
from smalls_data.gen.payments import Gateway
from smalls_data.gen.postgres import Postgres
from smalls_data.gen.servicenow import ServiceNow
from smalls_data.gen.slack import Slack
from smalls_data.gen.splunk import Splunk


def build(scenario_file=SCENARIO / "larkspur-checkout.yaml", out_root=BUNDLES):
    """Render the bundle under `out_root` (the committed bundles/ by default; tests pass a tmp dir)."""
    ctx = load_ctx(scenario_file, out_root)
    if ctx.out.exists():
        shutil.rmtree(ctx.out)
    ctx.out.mkdir(parents=True)
    model = Model(ctx)
    for r in (Gateway(ctx, model), Checkout(ctx, model), Postgres(ctx, model), K8s(ctx, model), F5(ctx, model), Nginx(ctx, model), Splunk(ctx), SolarWinds(ctx),
              Datadog(ctx, model), Dynatrace(ctx), ServiceNow(ctx, model), Jenkins(ctx), PagerDuty(ctx), Slack(ctx, model), Confluence(ctx),
              PayCore(ctx), Triggers(ctx), Integration(ctx)):
        r.render()
    rows = sorted(ctx.rows, key=lambda r: (r.t_utc, r.path, str(r.locator)))
    p = ctx.out / "_internal" / "manifest.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(r.to_json() + "\n" for r in rows))
    key = content_path("answer_key.yaml").read_text()
    (ctx.out / "_internal" / "answer_key.yaml").write_text(f"# Answer key for {ctx.out.name}. Never served (PLAN 3.3).\n" + key)
    (ctx.out / "_internal" / "counts.json").write_text(json.dumps(dict(sorted(ctx.counts.items())), indent=2) + "\n")
    def per_unit(k, v):      # per-cart classes: records per distinct cart; otherwise the renderer's fixed count
        if k in ctx.pop_keys:
            return round(sum(v.values()) / len(ctx.pop_keys[k]), 3)
        return ctx.per_unit.get(k, 1)
    pop = {k: {"per_unit": per_unit(k, v), "by_minute": dict(sorted((m, round(n, 2)) for m, n in v.items()))}
           for k, v in sorted(ctx.population.items())}
    (ctx.out / "_internal" / "population.json").write_text(json.dumps(pop, indent=1) + "\n")
    return ctx


def main(argv: list[str] | None = None) -> int:
    """`python -m smalls_data.build [scenario file]`: the development scenario unless a file is named."""
    argv = sys.argv[1:] if argv is None else argv
    ctx = build(Path(argv[0])) if argv else build()
    print(f"data: built {ctx.out} with {len(ctx.rows)} manifest rows")
    for k, v in sorted(ctx.counts.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
