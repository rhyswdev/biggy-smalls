from smalls.contracts.handles import h8, ic_note_handle, is_handle, log_handle, slack_handle
from smalls.contracts.manifest import ManifestRow

PLAN_LINE = ('{"handle":"log:pay-02/3fa91c0e","src":"splunk","path":"splunk/raw/payments_app/pay-02.log","locator":4412,'
             '"t_utc":"2026-10-13T14:31:07.221Z","t_known":"2026-10-13T14:31:07.221Z","valid_from":null,"valid_to":null,'
             '"class":"log.hikari_timeout","sig_id":"sig.first_pool_timeout","kind":"signal"}')


def test_hand_written_manifest_line_validates():
    row = ManifestRow.from_json(PLAN_LINE)
    assert row.class_ == "log.hikari_timeout"
    assert ManifestRow.from_json(row.to_json()) == row


def test_handle_grammar_examples():
    for h in ["snow:INC0048811", "snow:CHG0031877", "dt:P-26101350", "pd:8812", "dd:ev7820011093", "dd:mon7391240",
              "jk:payments-gateway-deploy#412", "sw:55012", "conf:PGW-DB-CONN-TIMEOUT", "psp:paycore@v2",
              "log:pay-02/3fa91c0e", "log:pay-02/3fa91c0e~2", "slack:inc-checkout-latency/8b2e11f0", "q:7c1d02aa",
              "metric:postgresql.connections{host:pay-db-01}@1430-1452", "snow:ci/Payments-Gateway"]:
        assert is_handle(h), h
    assert not is_handle("log:pay-02/zz")


def test_handles_are_deterministic():
    a = log_handle("splunk/raw/payments_app/pay-02.log", "line")
    assert a == log_handle("splunk/raw/payments_app/pay-02.log", "line")
    assert a.startswith("log:pay-02/")
    assert log_handle("splunk/raw/payments_app/pay-02.log", "line", 2) == a + "~2"
    assert slack_handle("sre-oncall", "1.0", "x") == f"slack:sre-oncall/{h8('sre-oncall', '1.0', 'x')}"


def test_ic_note_handle_grammar():
    h = ic_note_handle("run-1", "2026-10-13T14:45:00Z", "hold the rollback")
    assert h == f"ic:note/{h8('run-1', '2026-10-13T14:45:00Z', 'hold the rollback')}"
    assert is_handle(h)
    assert h == ic_note_handle("run-1", "2026-10-13T14:45:00Z", "hold the rollback")
    assert h != ic_note_handle("run-2", "2026-10-13T14:45:00Z", "hold the rollback")
    for bad in ["ic:note/zz", "ic:note/3fa91c0", "ic:note/3fa91c0e~2", "ic:3fa91c0e", "ic:memo/3fa91c0e"]:
        assert not is_handle(bad), bad


def test_facts_cover_plan_aliases_and_people():
    import yaml
    facts = yaml.safe_load(open("scenario/facts.yaml"))
    aliases = {a for e in facts["entities"].values() for lst in e["aliases"].values() for a in lst}
    plan_aliases = ["checkout-api", "payments-gateway", "PaymentsGateway", "payments-gw", "pay-db-01", "PayCore",
                    "api.paycore.example", "pay-01", "pay-02", "pay-03", "pay-04", "k8s-prod-east", "redis-sessions",
                    "redis-sessions-01", "node-east-14", "search-indexer", "cdn.larkspur.com", "payments-reconciler",
                    "recon_app", "pgw_app", "win-rpt-05", "win-rpt-08", "Online Checkout", "Payments - Prod",
                    "Database - Prod", "Checkout - Prod", "payments-gw.prod.larkspur.internal", "PAY-03.LARKSPUR.INTERNAL",
                    "checkout-db", "ord-01", "orders_app", "Payments Gateway", "storefront-web"]
    missing = [a for a in plan_aliases if a not in aliases]
    assert not missing, missing
    names = {p["name"] for p in facts["people"]}
    assert names == {"Alice Nguyen", "Ben Kowalski", "Sam Ortega", "Elena Rossi", "Kofi Mensah", "Jordan Pike",
                     "Priya Raman", "Tom Beckett", "Dana Whitfield", "Marcus Okafor"}
