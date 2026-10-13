"""Templates and sampling (docs/decisions/ingestion-decisions.md, build row 5): every log record has a
template and a rate from the served sampling block, the template count is stable, and two ingests give the
same dump."""
import sqlite3

import pytest

import parity
from smalls.contracts.handles import h8
from smalls.tools import store
from smalls.tools.ingest import masker

BUNDLE = parity.BUNDLE


@pytest.fixture(scope="module")
def built():
    return store.build(BUNDLE)


def one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_every_log_record_has_a_template_and_a_rate(built):
    assert one(built.conn, "SELECT count(*) FROM record WHERE kind = 'log'") == 3989
    assert one(built.conn, "SELECT count(*) FROM record WHERE kind = 'log' AND (template_id IS NULL OR keep_1_in IS NULL OR sample_per IS NULL)") == 0
    assert one(built.conn, "SELECT count(*) FROM record WHERE kind != 'log' AND (template_id IS NOT NULL OR keep_1_in IS NOT NULL)") == 0
    # The stored id is the hash of the sourcetype and the masked title, so a tool can show the template.
    for title, sourcetype, template_id in built.conn.execute("SELECT title, json_extract(attrs, '$.sourcetype'), template_id FROM record WHERE kind = 'log'"):
        assert template_id == h8(sourcetype, masker.mask(title))


def test_sampling_rates_come_from_the_served_block_and_class_names_never_reach_a_column(built):
    served = built.integration.sampling
    for source, block in served.items():
        rates = {(c["keep_1_in"], c["per"]) for c in block["classes"]}
        got = set(built.conn.execute("SELECT DISTINCT keep_1_in, sample_per FROM record WHERE source = ?", (source,)).fetchall())
        assert got and got <= rates, source
    names = {c["name"] for block in served.values() for c in block["classes"]} - {"request", "event", "connection", "other", "checkout", "submit"}
    # A pattern that is only a field match (`"level":"DEBUG"`) reads the same as the parsed field; the others cannot be anything else.
    patterns = {c["match"] for block in served.values() for c in block["classes"] if set(c["match"]) & set("\\(|[")}
    columns = "coalesce(sample_per,'') || ' ' || attrs || ' ' || flags || ' ' || tags || ' ' || coalesce(template_id,'') || ' ' || coalesce(status,'') || ' ' || coalesce(sev_text,'')"
    for (shown,) in built.conn.execute(f"SELECT DISTINCT {columns} FROM record WHERE kind = 'log'"):
        assert not [n for n in names if f'"{n}"' in shown or f" {n} " in f" {shown} "], shown[:200]
        assert not [p for p in patterns if p in shown]


def test_a_postgres_connection_line_takes_its_session_rate(built):
    rows = built.conn.execute("SELECT body, keep_1_in, json_extract(attrs, '$.pid') FROM record WHERE source = 'splunk/raw/postgres' ORDER BY rid").fetchall()
    by_pid: dict[int, set] = {}
    for body, rate, pid in rows:
        if "connection authorized" in body or "remaining connection slots" in body:
            by_pid.setdefault(pid, set()).add(rate)
    received = [(rate, pid) for body, rate, pid in rows if "connection received" in body]
    assert received and all({rate} == by_pid[pid] for rate, pid in received)
    assert {rate for rate, _ in received} == {1, 12}


def test_masker_keeps_what_tells_lines_apart():
    m = masker.mask
    assert m("2026-10-13 14:31:07,221 ERROR [http-nio-8443-exec-44] o.h.e.j.s.SqlExceptionHelper - HikariPool-1 - Connection is not available, "
             "request timed out after 5000ms (total=35, active=35, idle=0, waiting=12)") == \
        "<*> ERROR [<*>] o.h.e.j.s.SqlExceptionHelper - <*> - Connection is not available, request timed out after <*> (total=<*>, active=<*>, idle=<*>, waiting=<*>)"
    ok = m('81.43.1.114 - - [13/Oct/2026:13:45:03 +0000] "POST /api/checkout/submit?cartId=55674518 HTTP/1.1" 200 406 "https://x.example/pay" "App/7.12.0 (Android 14)" rt=0.496')
    bad = m('92.20.7.1 - - [13/Oct/2026:14:31:21 +0000] "POST /api/checkout/submit?cartId=55201698 HTTP/1.1" 504 167 "https://x.example/pay" "Mozilla/5.0 (Macintosh)" rt=10.003')
    assert "POST /api/checkout/submit 200" in ok and "POST /api/checkout/submit 504" in bad and ok != bad
    assert ok.replace("200", "N") == bad.replace("504", "N")      # same line but for the status
    assert m("INFO c.x.RequestLogFilter - GET /orders/8812/items 404 12ms") == "INFO c.x.RequestLogFilter - GET /orders/<*>/items 404 <*>"
    assert m("Pool /Common/pool_a member /Common/10.20.4.33:8443 monitor status down. [ was up for 4hr:13mins:36sec ]") == \
        "Pool /Common/pool_a member /Common/<*> monitor status down. [ was up for <*> ]"
    assert m("After cleanup  stats (total=6, active=1)") == "After cleanup  stats (total=<*>, active=<*>)"
    assert m(m(ok)) == m(ok)


def test_templates_are_stable_and_few(built):
    again = store.build(BUNDLE)
    sql = "SELECT template_id, count(*) FROM record WHERE kind = 'log' GROUP BY template_id ORDER BY template_id"
    assert built.conn.execute(sql).fetchall() == again.conn.execute(sql).fetchall()
    assert built.report.templates == one(built.conn, "SELECT count(DISTINCT template_id) FROM record") == again.report.templates
    assert 50 <= built.report.templates <= 200


def test_two_ingests_give_the_same_dump(built):
    assert built.dump_hash() == store.build(BUNDLE).dump_hash()


def test_dump_writes_a_file_that_reads_back(built, tmp_path):
    built.dump(tmp_path / "x.db")
    back = sqlite3.connect(tmp_path / "x.db")
    assert back.execute("SELECT count(*) FROM record").fetchone()[0] == one(built.conn, "SELECT count(*) FROM record")
    assert back.execute("SELECT count(*) FROM visible").fetchone()[0] == 0
