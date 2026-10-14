"""The proximity baseline (row Z4 of docs/decisions/eval-loop.md section 15): same item first, then a CMDB
neighbour, then the rest; within a tier the change nearest the first alert, up to AFTER_MIN after it."""
from eval import proximity

H = 3_600_000


def unit(name, ci, start):
    return {"id": name, "handle": name, "ci": ci, "start": start, "title": name, "builds": []}


def test_the_same_item_outranks_a_neighbour_and_a_neighbour_outranks_time_alone():
    t0 = 10 * H
    units = [unit("near", None, t0 - 60_000), unit("neighbour", "db", t0 - 2 * H), unit("same", "api", t0 - 5 * H),
             unit("just-after", "api", t0 + 5 * 60_000), unit("too-late", "api", t0 + 20 * 60_000)]
    neighbours = {"api": {"db"}, "db": {"api"}}
    ranked = [u["id"] for _, _, u in proximity.rank(units, "api", t0, 6, neighbours)]
    assert ranked == ["just-after", "same", "neighbour", "near"]
    assert [u["id"] for _, _, u in proximity.rank(units, "api", t0, 1, neighbours)] == ["just-after", "near"]


def test_names_resolve_across_vendor_spellings():
    assert proximity.norm("service:checkout-api") == proximity.norm("Checkout API") == "checkoutapi"
    assert proximity.norm("host:pay-02") == proximity.norm("PAY-02.EXAMPLE.INTERNAL") == "pay02"
