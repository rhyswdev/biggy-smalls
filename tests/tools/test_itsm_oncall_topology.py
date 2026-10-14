"""`itsm_query`, `get_oncall` and `get_topology` (build row D7; section 6.4, K1 and K9 of
docs/decisions/tools-agent-build.md). Acceptance: golden per table, and display values match the valid
version; golden at each checkpoint; a two-hop traversal returns each edge once with its source, every
resolved alias is correct, and recall is printed, not asserted."""
import json
import re

import pytest
import yaml

import golden
import parity
from smalls.tools import derive, get_topology, windows

CHECKPOINTS = list(golden.CHECKPOINTS)
TABLES = {"incident": "incident", "journal": "sys_journal_field", "user": "sys_user", "group": "sys_user_group"}


def paths_of(ctx, dataset: str) -> set[str]:
    return {p for (p,) in ctx.store.conn.execute("SELECT DISTINCT path FROM record WHERE dataset = ?", (dataset,))}


def pair(name: str, value: str) -> dict:
    return {"name": name, "value": value}


# -- itsm_query --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", list(TABLES))
@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_each_table_lists_the_rows_visible_then_newest_first(checkpoint, table):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    paths = paths_of(ctx, TABLES[table])
    want = {row["handle"] for row in golden.visible(now) if row["path"] in paths}             # from the manifest's times alone
    event = dict(ctx.store.conn.execute("SELECT handle, t_event FROM visible WHERE dataset = ?", (TABLES[table],)))
    env = golden.run("itsm_query", ctx, table=table)
    assert env.status == "ok" and env.meta["total"] == len(want) > 0 and set(env.shown) <= want
    assert len(env.rows) == min(len(want), 20) and len(env.text) // 4 <= 3000
    times = [event[h] for h in golden.handles(env)]
    assert times == sorted(times, reverse=True) and min(times) >= max((event[h] for h in want - set(env.shown)), default=0)      # newest first
    if len(want) > 20:
        assert f"[showing 20 of {len(want)} {table} rows: the oldest {len(want) - 20}, from " in env.text and env.meta["truncated"]
    assert all(row.text.startswith(row.handle + "  ") and parity.ms(row.t_known) < now for row in env.rows)
    assert env.text.splitlines()[-1].startswith(f"[sysparm_query] GET /api/now/table/{TABLES[table]}?sysparm_query=ORDERBYDESC")
    one = golden.run("itsm_query", ctx, table=table, limit=1)
    assert golden.handles(one) == golden.handles(env)[:1] and golden.run("itsm_query", ctx, table=table, limit=21).meta["error"] == "arguments"
    assert golden.run("itsm_query", ctx, table="change").meta["error"] == "arguments"


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_an_open_incident_shows_the_display_values_of_the_version_valid_then(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    paths = paths_of(ctx, "incident")
    versions: dict[str, list[dict]] = {}
    for row in golden.manifest():
        if row["path"] in paths and (row["valid_from"] or row["valid_to"]):
            versions.setdefault(row["handle"], []).append(row)
    assert versions
    fields = ["state", "priority", "assigned_to", "assignment_group", "sys_mod_count", "sys_updated_by"]
    checked = 0
    for handle, rows in versions.items():
        valid = [v for v in rows if parity.visible_at(v, now)]
        if not valid:
            continue
        assert len(valid) == 1
        served = json.loads(golden.served(valid[0]["path"]))["result"]
        number = served["number"]
        env = golden.run("itsm_query", ctx, table="incident", filters=[pair("number", number)], fields=fields)
        assert env.shown == (handle,) and f"  {number}  {served['short_description']}  state {served['state']}  " in env.rows[0].text
        for name in fields:
            assert f"  {name} {served[name] or '(empty)'}" in env.rows[0].text, (handle, name)
        later = [v for v in rows if parity.ms(v["t_known"]) >= now]
        for v in later:                                                             # and nothing of a version not yet known
            newer = json.loads(golden.served(v["path"]))["result"]
            assert newer["sys_mod_count"] != served["sys_mod_count"] and f"sys_mod_count {newer['sys_mod_count']}" not in env.text
        checked += 1
        assert len(rows) > 1
    assert checked


def test_filters_are_equality_on_the_display_value_and_are_anded():
    ctx = golden.ctx()
    rows = [(h, json.loads(a)) for h, a in ctx.store.conn.execute("SELECT handle, attrs FROM visible WHERE dataset = 'incident'")]
    group = max({a["assignment_group"] for _, a in rows if a.get("assignment_group")}, key=lambda g: sum(1 for _, a in rows if a.get("assignment_group") == g))
    want = {h for h, a in rows if a.get("assignment_group") == group}
    env = golden.run("itsm_query", ctx, table="incident", filters=[pair("assignment_group", group.upper())])     # case is not compared
    assert set(env.shown) == want and 1 < len(want) < len(rows) and f"sysparm_query=assignment_group={group.upper()}^ORDERBYDESCopened_at" in env.native_query
    state = next(a["state"] for h, a in rows if h in want)
    both = golden.run("itsm_query", ctx, table="incident", filters=[pair("assignment_group", group), pair("state", state)])
    assert set(both.shown) == {h for h, a in rows if a.get("assignment_group") == group and a.get("state") == state}
    assert golden.run("itsm_query", ctx, table="incident", filters=[pair("assignment_group", group[:-1])]).meta["empty"]      # equality, not a prefix
    unknown = golden.run("itsm_query", ctx, table="incident", filters=[pair("no_such_field", "x")])
    assert unknown.meta["error"] == "unknown_value" and "filters.name is one of: " in unknown.text and "assignment_group" in unknown.text
    assert golden.run("itsm_query", ctx, table="incident", filters=[pair("x", "y")] * 7).meta["error"] == "arguments"
    # text is a substring of the title and body, whatever the case
    title = ctx.store.conn.execute("SELECT title FROM visible WHERE dataset = 'incident' ORDER BY t_event DESC LIMIT 1").fetchone()[0]
    word = max(title.split(), key=len)
    found = golden.run("itsm_query", ctx, table="incident", text=word.upper())
    titles = dict(ctx.store.conn.execute("SELECT handle, lower(coalesce(title, '') || char(10) || coalesce(body, '')) FROM visible WHERE dataset = 'incident'"))
    assert set(found.shown) == {h for h, t in titles.items() if word.lower() in t} and f"123TEXTQUERY321={word.upper()}" in found.native_query
    empty = golden.run("itsm_query", ctx, table="incident", text="zz-no-such-text-zz", filters=[pair("state", state)])
    assert empty.meta["empty"] and empty.text.startswith("0 rows. Drop a filter or text=.")


def test_fields_adds_named_fields_and_a_wall_clock_time_is_shown_as_utc_with_its_native_value():
    ctx = golden.ctx()
    env = golden.run("itsm_query", ctx, table="incident", limit=1, fields=["opened_at", "caller_id", "no_such_field"])
    handle = env.shown[0]
    attrs = json.loads(ctx.store.conn.execute("SELECT attrs FROM visible WHERE handle = ?", (handle,)).fetchone()[0])
    opened = ctx.store.integration.parse("servicenow", attrs["opened_at"])[0]
    assert f"opened_at {windows.iso(opened)} (native {attrs['opened_at']})" in env.text
    assert f"caller_id {attrs['caller_id']}" in env.text and "no_such_field (empty)" in env.text           # asked for by name, so printed when empty
    assert env.native_query.endswith("&sysparm_fields=opened_at,caller_id,no_such_field")
    plain = golden.run("itsm_query", ctx, table="incident", limit=1)
    assert "(empty)" not in plain.text and "caller_id" not in plain.text and "priority " in plain.text    # the default set leaves empty fields out
    users = golden.run("itsm_query", ctx, table="user", filters=[pair("active", "false")])
    inactive = [t for (t,) in ctx.store.conn.execute("SELECT title FROM visible WHERE kind = 'user' AND status = 'inactive'")]
    assert inactive and len(users.rows) == len(inactive) and all(f"  {name}  inactive" in users.text for name in inactive)
    groups = golden.run("itsm_query", ctx, table="group", filters=[pair("name", inactive[0])])
    assert groups.meta["empty"]
    named = golden.run("itsm_query", ctx, table="user", filters=[pair("name", inactive[0].lower())])
    assert len(named.rows) == 1 and inactive[0] in named.text


def test_a_journal_entry_is_found_by_its_incidents_number_and_shortened_with_a_mark():
    ctx = golden.ctx()
    parent, n = ctx.store.conn.execute("SELECT parent, count(*) FROM visible WHERE dataset = 'sys_journal_field' GROUP BY 1 ORDER BY 2 DESC").fetchone()
    number = ctx.store.conn.execute("SELECT native_id FROM visible WHERE handle = ?", (parent,)).fetchone()[0]
    env = golden.run("itsm_query", ctx, table="journal", filters=[pair("number", number)])
    mine = [h for (h,) in ctx.store.conn.execute("SELECT handle FROM visible WHERE dataset = 'sys_journal_field' AND parent = ?", (parent,))]
    assert n > 1 and sorted(env.shown) == sorted(mine) and all(f" on {parent}  by " in row.text for row in env.rows)
    bodies = dict(ctx.store.conn.execute("SELECT handle, body FROM visible WHERE dataset = 'sys_journal_field'"))
    for row in env.rows:
        body = " ".join(bodies[row.handle].split())
        shown = row.text.split("\n    ", 1)[1]
        if len(body) <= 300:
            assert shown == body
        else:
            assert shown == f"{body[:300]} […+{len(body) - 300} chars]" and "get_record <handle> returns the whole entry" in env.text
            whole = "".join(golden.run("get_record", ctx, handle=row.handle, part=p).text for p in range(1, golden.run("get_record", ctx, handle=row.handle).meta["total"] + 1))
            assert " ".join(body[-80:].split()) in " ".join(whole.split())            # nothing is lost
    assert any(len(" ".join(b.split())) > 300 for h, b in bodies.items() if h in mine)
    # The incident's own row points at its journal entries.
    incident = golden.run("itsm_query", ctx, table="incident", filters=[pair("number", number)])
    assert incident.shown == (parent,) and sorted(incident.referenced) == sorted(mine) and "  journal snow:jn/" in incident.text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_the_signal_input_filters_incidents_and_says_how_many_carry_no_tag(checkpoint):
    off, on = golden.ctx(checkpoint, tags=False), golden.ctx(checkpoint, tags=True)
    plain, labelled = golden.run("itsm_query", off, table="incident"), golden.run("itsm_query", on, table="incident")
    assert "[tag " not in plain.text and re.sub(r" \[tag signal=[a-z_,]+\]", "", labelled.text) == plain.text
    assert golden.run("itsm_query", off, table="incident", signal="errors").meta["error"] == "arguments"       # the input exists only with tags on
    tags = {h: json.loads(t).get("signal", []) for h, t in on.store.conn.execute("SELECT handle, tags FROM visible WHERE dataset = 'incident'")}
    untagged = golden.run("itsm_query", on, table="incident", signal="untagged")
    assert set(untagged.shown) == {h for h, v in tags.items() if not v} and untagged.meta["tag"] == "signal=untagged"
    value = next(v[0] for v in tags.values() if v)
    env = golden.run("itsm_query", on, table="incident", signal=value)
    want = {h for h, v in tags.items() if value in v}
    assert set(env.shown) == want
    none = sum(1 for v in tags.values() if not v)
    assert f"[tag filter signal={value}: {len(want)} of the {len(tags)} incidents that match without it. {none} of those {len(tags)} carry no signal tag" in env.text
    assert golden.run("itsm_query", on, table="user", signal=value).meta["error"] == "arguments"


# -- get_oncall --------------------------------------------------------------------------------------------


def served_list(ctx, dataset: str, key: str) -> list[dict]:
    """The records of one served paging file: every object in it that carries `key`."""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if key in node and isinstance(node.get(key), (dict, list, str, int)) and ("id" in node or "start" in node):
                found.append(node)
            else:
                for value in node.values():
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(paths_of(ctx, dataset)):
        walk(json.loads(golden.served(path)))
    return found


@pytest.mark.parametrize("clock", [*CHECKPOINTS, "07:30", "23:30"])
def test_who_is_on_call_at_now_for_every_service_and_policy(clock):
    ctx = golden.ctx(clock)
    now = ctx.store.now
    shifts = served_list(ctx, "oncalls", "escalation_level")
    policies = {p["id"]: p for p in served_list(ctx, "escalation_policies", "escalation_rules")}
    services = [s for s in served_list(ctx, "services", "escalation_policy") if s.get("type") == "service" and parity.ms(s["created_at"]) < now]
    assert len(services) >= 3 and len(policies) >= 3 and len(shifts) >= 4
    listing = golden.run("get_oncall", ctx)
    assert listing.status == "ok" and all(f"service {s['name']}  " in listing.text for s in services) and len(listing.text) // 4 <= 500
    assert all(f"escalation policy {p['name']}  " in listing.text for p in policies.values()) and "  on call at now  " not in listing.text
    for service in services:
        policy = policies[service["escalation_policy"]["id"]]
        held = [s for s in shifts if s["escalation_policy"]["id"] == policy["id"] and parity.ms(s["start"]) <= now < parity.ms(s["end"])]
        for args in ({"service": service["name"]}, {"service": service["id"].lower()}, {"policy": policy["name"]}, {"policy": f"pd:ep/{policy['id']}"}):
            env = golden.run("get_oncall", ctx, **args)
            assert env.status == "ok" and len(env.text) // 4 <= 500, env.text
            on_call = [row.text for row in env.rows if "  on call at now  " in row.text]
            assert sorted(re.search(r"level (\d+)  (.+?)  policy ", line).groups() for line in on_call) == sorted(
                (str(s["escalation_level"]), s["user"]["summary"]) for s in held)
            assert all(f"since {s['start']}  planned end {s['end']}" in env.text for s in held)       # the end is a scheduled time, and says so
            rules = policy["escalation_rules"]
            assert f"escalation policy {policy['name']}  " in env.text and f"{len(rules)} level" in env.text
            for n, rule in enumerate(rules, 1):
                assert f"    level {n}: targets {', '.join(t['summary'] for t in rule['targets'])}; " in env.text
                assert f"after {rule['escalation_delay_in_minutes']} min" in env.text or f"waits {rule['escalation_delay_in_minutes']} min" in env.text
            missing = sorted(set(range(1, len(rules) + 1)) - {s["escalation_level"] for s in held})
            assert ("no visible shift holds at now for level" in env.text) == bool(missing)
            assert f"service {service['name']}  status " in env.text
    # A service's status is derived from the paging incidents open on it at now, never read from the served field.
    for service in services:
        states = [s for (s,) in ctx.store.conn.execute("SELECT status FROM visible WHERE kind = 'incident' AND json_extract(attrs, '$.service_id') = ?", (service["id"],))]
        want = "critical" if "triggered" in states else "warning" if "acknowledged" in states else "active"
        assert f"service {service['name']}  status {want} (derived at now: " in golden.run("get_oncall", ctx, service=service["name"]).text


def test_the_derived_status_changes_between_the_checkpoints_and_the_arguments_are_checked():
    first, second = golden.ctx(golden.FIRST), golden.ctx(golden.SECOND)
    names = [t for (t,) in second.store.conn.execute("SELECT title FROM visible WHERE dataset = 'services' ORDER BY 1")]
    status = lambda ctx, name: re.search(r"status (\w+) \(derived", golden.run("get_oncall", ctx, service=name).text).group(1)
    assert any(status(first, name) != status(second, name) for name in names)        # the same service, two clocks, two answers
    policy = second.store.conn.execute("SELECT title FROM visible WHERE dataset = 'escalation_policies' ORDER BY 1").fetchone()[0]
    both = golden.run("get_oncall", second, service=names[0], policy=policy)
    assert both.meta["error"] == "arguments" and "give service or policy, not both" in both.text
    unknown = golden.run("get_oncall", second, service="no-such-service")
    assert unknown.meta["error"] == "unknown_value" and all(name in unknown.text for name in names)
    assert golden.run("get_oncall", second, policy="no-such-policy").text.startswith("policy='no-such-policy' matches nothing visible. policy is one of: ")
    env = golden.run("get_oncall", second, service=names[0])
    assert env.referenced == () and env.text.splitlines()[-1].startswith("[pagerduty] GET /oncalls?escalation_policy_ids[]=")
    assert windows.iso(second.store.now) not in env.text                           # now is never printed


# -- get_topology: matching (K9) ---------------------------------------------------------------------------


def item(name: str, **attrs) -> dict:
    ids = [x for x in re.split(r"[,;\s]+", attrs.get("u_external_ids") or "") if x]
    return {"title": name, "attrs": attrs, "external_ids": ids}


def test_names_are_matched_by_six_methods_in_order_and_by_nothing_looser():
    box = item("App-Server", ip_address="10.0.0.7", u_external_ids="mon:app-srv, apm:AppServer")
    assert get_topology.match_item("App-Server", box) == "exact"
    assert get_topology.match_item("APP-SERVER", box) == "casefold"
    assert get_topology.match_item("app-server.corp.example", box) == "domain stripped"
    assert get_topology.match_item("APP-SERVER.CORP.EXAMPLE", box) == "domain stripped"
    assert get_topology.match_item("app-server-7f5c8d9b6-m4n7q", box) == "pod suffix stripped"
    assert get_topology.match_item("10.0.0.7", box) == "IP address" and get_topology.match_item("10.0.0.8", box) is None
    assert get_topology.match_item("app-srv", box) == "external id mon:app-srv"
    assert get_topology.match_item("apm:AppServer", box) == "external id apm:AppServer"
    assert get_topology.match_item("app-srv-6d8c9b7f4d-x2k1p", box) == "external id mon:app-srv, after pod suffix stripped"
    assert get_topology.match_item("app-srv.corp.example", box) == "external id mon:app-srv, after domain stripped"
    # Nothing looser: no spaces or hyphens dropped, no prefix, no part of a name.
    for near in ("AppServer2", "App Server", "appserver-", "app", "server", "App-Serve", "app-server-1", "app_server"):
        assert get_topology.match_item(near, box) is None, near
    assert get_topology.match_item("AppServer", box) == "external id apm:AppServer"
    assert [get_topology.rank(m) for m in ("exact", "casefold", "domain stripped", "pod suffix stripped", "IP address", "external id x:y, after z")] == [0, 1, 2, 3, 4, 5]
    # A name with its own domain is matched from either side, and an address is never stripped as a domain.
    assert get_topology.match_names("site", "site.example.com") == "domain stripped" and get_topology.match_names("10.0.0.7", "10") is None
    assert get_topology.forms("10.0.0.7") == [] and get_topology.forms("web-0") == [] and get_topology.match_names("", "x") is None
    source = open(get_topology.__file__, encoding="utf-8").read()
    assert not re.search(r"\.(internal|com|example|local|net)\b", source)           # no literal domain in the code


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_every_resolved_alias_is_correct_and_recall_is_printed(checkpoint, capsys):
    ctx = golden.ctx(checkpoint)
    facts = yaml.safe_load(open("scenario/facts.yaml", encoding="utf-8"))            # the expected table, read at test time
    groups = [{a for names in e["aliases"].values() for a in names} for e in facts["entities"].values()]
    items = {t for (t,) in ctx.store.conn.execute("SELECT title FROM visible WHERE kind = 'ci'")}
    resolved = wrong = total = 0
    for names in groups:
        if not names & items:
            continue                                                                # a thing the CMDB does not hold cannot resolve to an item
        for name in sorted(names):
            total += 1
            env = golden.run("get_topology", ctx, entity=name)
            matched = re.findall(r"CMDB item (.+?)  matched by ", env.text) if env.status == "ok" else []
            resolved += bool(matched)
            wrong += sum(1 for ci in matched if not any(name in other and ci in other for other in groups))
    assert wrong == 0 and resolved >= 20 and total > resolved                        # correct where it resolves; some names stay unresolved
    with capsys.disabled():
        print(f"\nalias recall at {checkpoint}: {resolved} of {total} names resolve to a CMDB item ({resolved / total:.0%}); none resolves to a wrong one")


# -- get_topology: edges and traversal ---------------------------------------------------------------------


def edge_rows(env) -> list[tuple[int, str, str, str, str, str]]:
    """(hop, from, type, to, source, handle) per edge row."""
    out = []
    for row in env.rows:
        m = re.match(r"(\S+)  hop (\d)  (.+?) -\[(.+?)\]-> (.+?)  source (.+?)  \[", row.text)
        if m:
            out.append((int(m[2]), m[3], m[4], m[5], m[6], m[1]))
    return out


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_two_hop_traversal_returns_each_edge_once_with_its_source(checkpoint):
    ctx = golden.ctx(checkpoint)
    rels = [(json.loads(a)["parent"], json.loads(a)["type"], json.loads(a)["child"], h) for h, a in ctx.store.conn.execute("SELECT handle, attrs FROM visible WHERE kind = 'rel'")]
    names = [t for (t,) in ctx.store.conn.execute("SELECT title FROM visible WHERE kind = 'ci' ORDER BY 1")]
    sources_seen = set()
    for name in names:
        one, two = golden.run("get_topology", ctx, entity=name, hops=1), golden.run("get_topology", ctx, entity=name, hops=2)
        assert one.status == two.status == "ok" and len(two.rows) <= 40 and len(two.text) // 4 <= 6000
        near, far = edge_rows(one), edge_rows(two)
        assert len(set((s, a, t, b) for _, a, t, b, s, _ in far)) == len(far)       # each edge once
        assert all(hop == 1 for hop, *_ in near) and [e for e in far if e[0] == 1] == near
        assert far == sorted(far, key=lambda e: (e[0], e[4], e[5]))                 # ordered by hop, then source
        assert all(row.text.startswith(row.handle + "  ") for row in two.rows)
        # Every CMDB relationship within two steps of the item, by the CMDB's own rows alone, is there, at that hop or nearer.
        first = [r for r in rels if name in (r[0], r[2])]
        ring = {x for r in first for x in (r[0], r[2])}
        second = [r for r in rels if r not in first and ring & {r[0], r[2]}]
        cmdb = {(a, t, b): hop for hop, a, t, b, s, _ in far if s.endswith("cmdb_rel_ci")}
        assert all(cmdb.get(r[:3]) == 1 for r in first) and all(cmdb.get(r[:3]) in (1, 2) for r in second)
        assert {h for *_, s, h in far if s.endswith("cmdb_rel_ci")} <= {r[3] for r in rels}       # an edge's row is the record that states it
        sources_seen |= {s for *_, s, _ in far}
        assert f"entity {name!r}" == two.text.splitlines()[0] and f"CMDB item {name}  matched by exact  status " in two.text
        assert re.search(rf"\[{len(far)} edges? within 2 hops", two.text) and ("[hops=2 follows" in one.text) == bool(near)
    assert len(sources_seen) == 3                                                  # the CMDB, monitored entities and service-call tags


def test_an_item_prints_its_owner_with_the_active_flag_and_no_more_than_the_plan_lists():
    ctx = golden.ctx()
    users = dict(ctx.store.conn.execute("SELECT title, status FROM visible WHERE kind = 'user'"))
    flags = set()
    for handle, name, attrs in ctx.store.conn.execute("SELECT handle, title, attrs FROM visible WHERE kind = 'ci'"):
        a = json.loads(attrs)
        line = next(row.text for row in golden.run("get_topology", ctx, entity=name).rows if row.handle == handle)
        flag = users.get(a["owned_by"], "no user record")
        flags.add(flag)
        ids = ", ".join(x for x in re.split(r"[,;\s]+", a.get("u_external_ids") or "") if x) or "(none)"
        assert line == (f"{handle}  CMDB item {name}  matched by exact  status {a['operational_status']}  owner {a['owned_by']} ({flag})  "
                        f"support group {a['support_group']}  external ids {ids}")
    assert {"active", "inactive"} <= flags                                         # an owner who has left is shown as such


def test_sources_are_printed_side_by_side_and_an_unmatched_name_stays_unresolved():
    ctx = golden.ctx()
    tagged = [json.loads(a)["tags"] for (a,) in ctx.store.conn.execute("SELECT attrs FROM visible WHERE json_type(attrs, '$.tags') = 'array'")]
    calls = {(dict(t.split(":", 1) for t in tags if ":" in t).get("service"), dict(t.split(":", 1) for t in tags if ":" in t).get("peer.service")) for tags in tagged}
    caller, callee = next((a, b) for a, b in calls if a and b)
    stating = sorted(h for h, a in ctx.store.conn.execute("SELECT handle, attrs FROM visible WHERE json_type(attrs, '$.tags') = 'array'")
                     if f"service:{caller}" in json.loads(a)["tags"] and f"peer.service:{callee}" in json.loads(a)["tags"])
    env = golden.run("get_topology", ctx, entity=callee, hops=1)
    edges = [e for e in edge_rows(env) if e[2] == "calls" and e[4].endswith(" tags")]
    assert edges == [(1, caller, "calls", callee, edges[0][4], stating[0])]          # several records state it: the lowest handle is the row
    assert len(stating) > 1 and set(stating[1:]) <= set(env.referenced) and f"also stated by {' '.join(stating[1:])}" in env.text
    resolves = bool(re.findall(r"CMDB item (.+?)  matched by ", env.text))
    if not resolves:
        assert f"no CMDB item matched {callee!r} by any method (exact, casefold, domain stripped, pod suffix stripped, IP address, external id): it stays unresolved" in env.text
        assert f"[{callee}: no CMDB item matched]" in env.text and f"service tag {callee!r} matched by exact" in env.text
    unknown = golden.run("get_topology", ctx, entity="zz-no-such-thing-zz")
    assert unknown.status == "error" and unknown.meta["error"] == "unknown_value" and "entity is one of: " in unknown.text
    assert golden.run("get_topology", ctx, entity=callee, hops=3).meta["error"] == "arguments"
    # A monitored entity is found by its name and by its id, and says which item it is, or that it is none.
    for handle, native_id, title in ctx.store.conn.execute("SELECT handle, native_id, title FROM visible WHERE kind = 'entity'"):
        for asked in (title, native_id):
            found = golden.run("get_topology", ctx, entity=asked)
            line = next(row.text for row in found.rows if row.handle == handle and "monitored entity" in row.text)
            assert f"monitored entity {title}  matched by {'exact id' if asked == native_id and asked != title else 'exact'}  type " in line
            assert re.search(rf"  {re.escape(title)}: (CMDB item|no CMDB item matched|matches \d+ CMDB items)", line)
    assert env.text.splitlines()[-1].startswith("[native query] (no single native query: ")


def test_an_edge_is_there_exactly_when_the_record_that_states_it_is_visible():
    late = golden.ctx()
    handle, known, attrs = late.store.conn.execute("SELECT handle, t_known, attrs FROM visible WHERE kind = 'rel' ORDER BY t_known DESC LIMIT 1").fetchone()
    parent = json.loads(attrs)["parent"]
    assert handle in golden.run("get_topology", late, entity=parent).shown
    before = golden.ctx(windows.iso(known - 60_000))
    early = golden.run("get_topology", before, entity=parent)
    assert handle not in early.shown and handle not in early.referenced
    assert early.status == "ok" or early.meta["error"] == "unknown_value"
