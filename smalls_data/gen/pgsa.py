"""Elena's pg_stat_activity output (data fix 7): one renderer for every place it is pasted."""

from __future__ import annotations

from datetime import timedelta

from smalls_data.gen.core import Ctx, rng
from smalls_data.gen.model import Model


def pgsa_text(ctx: Ctx, model: Model) -> tuple[str, str]:
    """(grouped summary, five oldest sessions): Elena's two psql outputs at 14:47:12, the same text in her
    Slack paste and in the INC0048811 work note. The pids are live backends from the Postgres renderer."""
    s = ctx.scenario["model"]["connections"]["pgsa_snapshot"]
    at = ctx.clock.t(s["at"])
    hdr = " usename    | application_name    | state               | count | oldest_xact"
    sep = "------------+---------------------+---------------------+-------+-------------"
    rows = [("pgw_app", "payments-gateway", "idle in transaction", s["gateway_idle_in_txn"], f"00:00:{s['oldest_xact_age_s']:02d}"),
            ("recon_app", "payments-reconciler", "active", s["recon_active"], "00:17:12"),
            ("orders_app", "order-service", "idle", s["order_idle"], ""),
            ("pgw_app", "payments-gateway", "active", s["gateway_active"], f"00:00:{s['oldest_xact_age_s'] - 2:02d}"),
            ("postgres", "psql", "active", 1, "00:00:00")]
    lines = ["payments=# select usename, application_name, state, count(*), max(now() - xact_start) as oldest_xact",
             "payments-#   from pg_stat_activity where backend_type = 'client backend' group by 1, 2, 3 order by 4 desc;", hdr, sep]
    for u, a, st, n, o in rows:
        lines.append(f" {u:<10} | {a:<19} | {st:<19} | {n:>5} | {o}")
    lines.append(f"({len(rows)} rows)")
    summary = "\n".join(lines)
    r = rng(ctx.seed, "pgsa")
    cols = ["pid", "usename", "application_name", "client_addr", "state", "xact_start", "wait_event", "query"]
    net = ctx.facts["network"]
    out = []
    for k, (pid, host) in enumerate(model.pgsa_pids):      # live backends from the Postgres renderer
        xs = at - timedelta(seconds=s["oldest_xact_age_s"] - k * 0.4 - r.random() * 0.3)
        out.append([str(pid), "pgw_app", "payments-gateway", net[host], "idle in transaction",
                    xs.strftime("%H:%M:%S.") + f"{xs.microsecond // 1000:03d}", s["wait_event"],
                    "insert into authorization_attempt (amount,currency,order_id,psp,status,attempt_no,created_at) values ($1,$2,$3,$4,$5,$6,$7)"])
    widths = [max(len(c), *(len(row[i]) for row in out)) for i, c in enumerate(cols)]
    fmt = lambda vals: " " + " | ".join(v.ljust(w) for v, w in zip(vals, widths))
    rows_txt = "\n".join(["payments=# select pid, usename, application_name, client_addr, state, xact_start::time, wait_event, query",
                          "payments-#   from pg_stat_activity where state = 'idle in transaction' order by xact_start limit 5;",
                          fmt(cols), "-" + "-+-".join("-" * w for w in widths)] + [fmt(row) for row in out] + ["(5 rows)"])
    return summary, rows_txt
