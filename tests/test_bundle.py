"""The committed bundle: it is what `make data` builds (rebuilt into tmp_path, never in place), the lint
is clean on it, its anchored signal lines land where PLAN 1.4 puts them, and its handles, times and
Postgres pids hold together. Every test reads the committed bundle by absolute path."""
import filecmp
import json
import re
import shutil
from pathlib import Path

import pytest

from smalls_data import shape
from smalls_data.build import build
from smalls_data.gen.core import BUNDLES, SCENARIO
from smalls_data.lint import Lint
from smalls_data.parity import Parity

B = BUNDLES / "larkspur-checkout-2026-10-13"


def test_committed_bundle_is_what_make_data_builds(tmp_path):
    build(out_root=tmp_path)
    fresh = tmp_path / B.name
    def walk(root):
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert walk(fresh) == walk(B)
    changed = [rel for rel in walk(B) if not filecmp.cmp(B / rel, fresh / rel, shallow=False)]
    assert not changed, f"committed bundle is stale; run make data: {changed[:5]}"


def test_lint_clean():
    L = Lint(SCENARIO / "larkspur-checkout.yaml")
    L.run()
    assert not L.errors, L.errors[:10]


def test_anchored_signals():
    pay01 = (B / "splunk/raw/payments_app/pay-01.log").read_text()
    assert "2026-10-13 14:26:12,044 WARN  [http-nio-8443-exec-17] c.l.p.psp.RetryPolicy - authorize attempt 1/4 timed out after 8000ms (txn=ord-7781901)" in pay01
    assert "2026-10-13 14:26:34,031 WARN  [HikariPool-1 housekeeper] c.z.h.pool.ProxyLeakTask - Connection leak detection triggered for org.postgresql.jdbc.PgConnection@5b2a8e1d on thread http-nio-8443-exec-17" in pay01
    pay02 = (B / "splunk/raw/payments_app/pay-02.log").read_text()
    assert "2026-10-13 14:31:07,221 ERROR [http-nio-8443-exec-44] o.h.e.j.s.SqlExceptionHelper - HikariPool-1 - Connection is not available, request timed out after 5000ms (total=35, active=35, idle=0, waiting=12)" in pay02
    assert "2026-10-13 14:31:34,009 DEBUG [HikariPool-1 housekeeper] c.z.h.pool.HikariPool - HikariPool-1 - After cleanup  stats (total=35, active=35, idle=0, waiting=12)" in pay02
    first_timeout = [ln for ln in pay02.splitlines() if "request timed out after 5000ms" in ln and "SqlExceptionHelper" in ln][0]
    assert first_timeout.startswith("2026-10-13 14:31:07,221")
    co = "\n".join(p.read_text() for p in (B / "splunk/raw/checkout_api").glob("*q8m4z.jsonl"))
    assert '"@timestamp":"2026-10-13T14:26:14.052Z"' in co and "ord-7781901" in co
    pg = (B / "splunk/raw/postgres/pay-db-01.log").read_text()
    assert pg.count("recon_app@payments LOG:  connection authorized") == 40
    assert "2026-10-13 14:33:02.118 UTC" in pg
    assert re.search(r"2026-10-13 14:34:05\.3\d\d UTC \[\d+\] postgres@payments LOG:  connection authorized", pg)


def test_manifest_handles_unique_per_version():
    rows = [json.loads(x) for x in (B / "_internal/manifest.jsonl").read_text().splitlines()]
    seen = set()
    for r in rows:
        key = (r["handle"], r["valid_from"], r["locator"])
        assert key not in seen, r
        seen.add(key)


def test_every_handle_and_t_known_rederives():
    assert Parity(B).run() == []


def test_parity_catches_a_t_known_served_nowhere(tmp_path):
    copy = tmp_path / B.name
    shutil.copytree(B, copy)
    man = copy / "_internal/manifest.jsonl"
    rows = man.read_text().splitlines()
    i = next(k for k, x in enumerate(rows) if '"path":"splunk/alerts/' in x)
    r = json.loads(rows[i])
    r["t_known"] = "2026-10-13T14:32:40.000Z"
    rows[i] = json.dumps(r, separators=(",", ":"))
    man.write_text("\n".join(rows) + "\n")
    assert any("does not re-derive" in p for p in Parity(copy).run())


def test_postgres_pids_rise_with_time_and_name_live_backends():
    rx = re.compile(r"^(\S+ \S+) UTC \[(\d+)\] \S+ \w+:  (.*)$")
    lines = [rx.match(x).groups() for x in (B / "splunk/raw/postgres/pay-db-01.log").read_text().splitlines()]
    received = [(t, int(p)) for t, p, m in lines if m.startswith("connection received")]
    assert [p for _, p in sorted(received)] == sorted(p for _, p in received)
    connected = {int(p) for _, p, m in lines if m.startswith("connection authorized")}
    first_logged = min(p for _, p in received)
    killed = {int(p) for _, p, m in lines if m.startswith("terminating connection")}
    eof = {int(p) for _, p, m in lines if m.startswith("unexpected EOF")}
    assert len(killed) == 30 and not killed & eof
    for p in killed | eof:     # a logged connection, or a backend opened before the log window
        assert p in connected or p < first_logged, p
    paste = next(m["text"] for m in json.loads((B / "slack/inc-checkout-latency/2026-10-13.json").read_text()) if "oldest ones" in m["text"])
    for p in map(int, re.findall(r"\n (\d{5}) ", paste)):
        assert p in connected or p < first_logged, p


def test_band_catches_a_rate_that_changes_mid_window():
    # 60 retried requests a minute, 4 lines each, at 1 in 100: kept is about 2.4 lines a minute.
    pop = [240.0] * 10
    honest = [4.0, 0, 0, 4.0, 0, 0, 0, 4.0, 0, 4.0]
    assert shape.within_band(sum(honest), sum(pop), 100, 4)
    # The defect fix 1 removed: everything kept for two minutes, then 1 in 100.
    assert not shape.within_band(sum(pop[:2]), sum(pop[:2]), 100, 4)


def test_served_triggers_are_not_a_source():
    integ = (B / "integration.yaml").read_text()
    assert "triggers" not in integ


def test_answer_key_refs_must_be_visible_at_their_checkpoint(tmp_path):
    copy = tmp_path / B.name
    shutil.copytree(B, copy)
    key = copy / "_internal/answer_key.yaml"
    key.write_text(key.read_text().replace("F.prior_timeout:   {any_of: [sig.inc0045210]}", "F.prior_timeout:   {any_of: [sig.psp_v2]}"))
    L = Lint(SCENARIO / "larkspur-checkout.yaml", bundle_root=tmp_path)
    L.inv6_answer_key()
    assert any("sig.psp_v2 has no manifest row visible at 14:52" in e for e in L.errors), L.errors


def test_service_model_designed_defects():
    cis = {c["name"]: c for c in json.loads((B / "servicenow/cmdb_ci.json").read_text())["result"]}
    rels = json.loads((B / "servicenow/cmdb_rel_ci.json").read_text())["result"]
    users = {u["name"]: u for u in json.loads((B / "servicenow/sys_user.json").read_text())["result"]}
    assert cis["Payments Gateway"]["owned_by"] == "Marcus Okafor" and users["Marcus Okafor"]["active"] == "false"
    assert cis["redis-sessions-01"]["install_status"] == "Retired"
    assert any(r["parent"] == "Checkout API" and r["child"] == "redis-sessions-01" for r in rels)
    assert not any({r["parent"], r["child"]} & {"Payments Gateway", "Card Payments"} and {r["parent"], r["child"]} & {"Checkout API", "Online Checkout"}
                   for r in rels)
    assert not any("PayCore" in name for name in cis)
