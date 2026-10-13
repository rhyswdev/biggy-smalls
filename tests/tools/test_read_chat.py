"""`read_chat` (build row B4; section 6.4, K1 and K4 of docs/decisions/tools-agent-build.md): the reply
count at each checkpoint equals the visible replies, a withheld field never appears, and a channel with no
visible message is not listed. Expected values come from the served export and the manifest's times."""
import json
import re
from functools import lru_cache

import pytest

import golden
import parity
import test_leak
from smalls.tools import inventory, windows

CHECKPOINTS = list(golden.CHECKPOINTS)
WITHHELD = ("reply_count", "reply_users", "reply_users_count", "latest_reply", "reactions", "members")


@lru_cache(maxsize=None)
def served_messages() -> tuple[dict, ...]:
    """Every served message with its manifest row: channel, handle, known time, and the message as exported."""
    out = []
    files: dict[str, list] = {}
    for row in golden.manifest():
        parts = row["path"].split("/")
        if parts[0] != "slack" or len(parts) != 3:
            continue
        doc = files.setdefault(row["path"], json.loads(golden.served(row["path"])))
        message = doc[int(str(row["locator"]).strip("/"))]
        out.append({"handle": row["handle"], "channel": parts[1], "known": parity.ms(row["t_known"]), "m": message,
                    "reply": bool(message.get("thread_ts")) and message["thread_ts"] != message["ts"]})
    return tuple(sorted(out, key=lambda x: x["known"]))


def everything(ctx, **args):
    return golden.run("read_chat", ctx, since="-30d", **args)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_golden_messages_in_a_window(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    since = now - 3_600_000
    want = [x["handle"] for x in served_messages() if since <= x["known"] < now and not x["reply"]]
    env = golden.run("read_chat", ctx, since="-1h")
    assert golden.handles(env) == want and want                                    # top-level messages, oldest first
    assert all(row.text.startswith(row.handle + "  ") and parity.ms(row.t_known) < now for row in env.rows)
    by_handle = {x["handle"]: x for x in served_messages()}
    for row in env.rows:
        x = by_handle[row.handle]
        assert f"  ts {x['m']['ts']}  #{x['channel']}  " in row.text and windows.iso(x["known"]) in row.text
    channel = by_handle[want[0]]["channel"]
    one = golden.run("read_chat", ctx, since="-1h", channel=channel)
    assert golden.handles(one) == [h for h in want if by_handle[h]["channel"] == channel]
    assert golden.handles(golden.run("read_chat", ctx, since="-1h", channel="#" + channel)) == golden.handles(one)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_the_reply_count_equals_the_visible_replies_never_the_exported_summary(checkpoint):
    ctx = golden.ctx(checkpoint)
    now = ctx.store.now
    rows = {row.handle: row.text for row in everything(ctx).rows}
    roots = [x for x in served_messages() if x["known"] < now and x["m"].get("reply_count")]
    assert roots
    stale = 0
    for root in roots:
        replies = [x for x in served_messages() if x["reply"] and x["m"]["thread_ts"] == root["m"]["ts"] and x["channel"] == root["channel"] and x["known"] < now]
        shown = re.search(r"\[replies (\d+) by ([^;]+); latest (\S+)\]", rows[root["handle"]])
        if not replies:
            assert shown is None
        else:
            assert int(shown.group(1)) == len(replies) and shown.group(3) == windows.iso(max(r["known"] for r in replies))
            assert len(shown.group(2).split(", ")) == len({r["m"]["user"] for r in replies})
        stale += len(replies) != root["m"]["reply_count"]
    assert stale > 0 or checkpoint != golden.FIRST                                 # the export's own count would have been wrong


@pytest.mark.parametrize("tags", [False, True])
@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_withheld_field_never_appears_and_members_are_never_shown(checkpoint, tags):
    ctx = golden.ctx(checkpoint, tags=tags)
    members = {m for channel in json.loads(golden.served("slack/channels.json")) for m in channel["members"]}
    for env in (everything(ctx), golden.run("read_chat", ctx, since="-2h"), inventory.build(ctx)):
        text = json.dumps(env.as_dict(), ensure_ascii=False)
        assert not [name for name in WITHHELD if name in text]
        assert not [m for m in members if m in text] and "member" not in text.lower()


def test_a_channel_with_no_visible_message_is_not_listed():
    first = {}
    for x in served_messages():
        first.setdefault(x["channel"], x["known"])
    late_channel, opened = max(first.items(), key=lambda kv: kv[1])
    assert late_channel in {c["name"] for c in json.loads(golden.served("slack/channels.json"))}      # the channel file names it at any time
    before = golden.ctx(windows.iso(opened - 60_000))
    listed = [c for c in first if first[c] < before.store.now]
    assert listed and late_channel not in listed
    refusal = everything(before, channel=late_channel)
    unknown = everything(before, channel="no-such-channel")
    assert refusal.status == "error" and refusal.text.replace(late_channel, "X") == unknown.text.replace("no-such-channel", "X")
    assert late_channel not in unknown.text and all(c in unknown.text for c in listed)
    assert late_channel not in everything(before).text and late_channel not in inventory.build(before).text
    after = golden.ctx(windows.iso(opened + 60_000))
    assert late_channel in inventory.build(after).text and everything(after, channel=late_channel).status == "ok"


def test_mentions_are_resolved_from_the_users_file():
    ctx = golden.ctx()
    users = {u["id"]: u for u in json.loads(golden.served("slack/users.json"))}
    mentioning = [x for x in served_messages() if x["known"] < ctx.store.now and re.search(r"<@U", x["m"].get("text", "")) and not x["reply"]]
    assert mentioning
    rows = {row.handle: row.text for row in everything(ctx).rows}
    for x in mentioning:
        for uid in re.findall(r"<@([A-Z0-9]+)>", x["m"]["text"]):
            name = users[uid]["profile"].get("display_name") or users[uid]["real_name"]
            assert f"@{name}" in rows[x["handle"]]
        assert "<@U" not in rows[x["handle"]]
    author = users[mentioning[0]["m"]["user"]]
    assert f"  {author['profile'].get('display_name') or author['real_name']}: " in rows[mentioning[0]["handle"]]


def test_replies_are_pointers_until_the_thread_is_asked_for():
    ctx = golden.ctx()
    now = ctx.store.now
    root = next(x for x in served_messages() if x["known"] < now and x["m"].get("reply_count"))
    replies = [x["handle"] for x in served_messages() if x["reply"] and x["m"]["thread_ts"] == root["m"]["ts"] and x["known"] < now]
    listing = everything(ctx)
    assert set(replies) <= set(listing.referenced) and not set(replies) & set(listing.shown)
    row = next(r.text for r in listing.rows if r.handle == root["handle"])
    assert f"[thread_ts={root['m']['ts']} shows them: {' '.join(replies)}]" in row
    thread = everything(ctx, thread_ts=root["m"]["ts"])
    assert golden.handles(thread) == [root["handle"], *replies] and thread.referenced == ()
    assert all("[a reply in thread " in r.text for r in thread.rows[1:])
    missing = everything(ctx, thread_ts="1000000000.000001")
    assert missing.meta["empty"] and "thread_ts is the ts printed on a message row" in missing.text


def test_text_also_matches_replies_and_a_matching_reply_is_its_own_row():
    ctx = golden.ctx()
    reply = next(x for x in served_messages() if x["reply"] and x["known"] < ctx.store.now)
    word = max(reply["m"]["text"].split(), key=len)
    env = everything(ctx, text=word.upper())
    assert reply["handle"] in env.shown
    for row in env.rows:
        assert word.lower() in row.text.lower()
    assert everything(ctx, text="zz-no-such-word-zz").text.startswith("0 rows. Drop text=, or widen the window.")


def test_a_cut_keeps_the_newest_messages_and_says_how_many_older_ones_it_dropped():
    ctx = golden.ctx()
    top_level = [x["handle"] for x in served_messages() if x["known"] < ctx.store.now and not x["reply"]]
    env = everything(ctx)
    assert len(top_level) > 60 and len(env.rows) <= 60
    assert golden.handles(env) == top_level[-len(env.rows):]                       # the newest survive
    assert f"[showing {len(env.rows)} of {len(top_level)} messages: the oldest {len(top_level) - len(env.rows)}, from " in env.text
    assert env.text.splitlines()[-1].startswith("[chat search] ")


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_nothing_from_after_now(checkpoint):
    ctx = golden.ctx(checkpoint, tags=True)
    now = ctx.store.now
    for env in (everything(ctx), golden.run("read_chat", ctx, since="-10m", until="2030-01-01T00:00:00Z")):
        text = json.dumps(env.as_dict(), ensure_ascii=False)
        assert max(test_leak.times_in(env.text, test_leak.UTC), default=0) <= now
        assert all(parity.ms(row.t_known) < now for row in env.rows)
        assert not [tok for tok in test_leak.SCENARIO["future_tokens"][checkpoint] if tok in text]


def test_tags_label_a_message_filter_it_and_say_what_the_filter_left_out():
    off, on = golden.ctx(tags=False), golden.ctx(tags=True)
    plain, tagged = golden.run("read_chat", off, since="-2h"), golden.run("read_chat", on, since="-2h")
    assert "[tag " not in plain.text and "[tag act=" in tagged.text
    assert re.sub(r" \[tag [a-z_]+=[a-z_,]+\]", "", tagged.text) == plain.text and tagged.shown == plain.shown
    assert golden.run("read_chat", off, since="-2h", act="hypothesis").meta["error"] == "arguments"
    since = on.store.now - 2 * 3_600_000
    acts = {h: json.loads(t).get("act", []) for h, t in on.store.conn.execute(
        "SELECT handle, tags FROM visible WHERE kind = 'message' AND parent IS NULL AND t_known >= ?", (since,))}
    untagged = [h for h, values in acts.items() if not values]
    assert untagged
    for value in ("hypothesis", "automated", "untagged"):
        env = golden.run("read_chat", on, since="-2h", act=value)
        want = {h for h, values in acts.items() if (not values if value == "untagged" else value in values)}
        assert set(env.shown) <= want and env.meta["total"] == len(want) and want
        assert (f"[tag filter act={value}: {len(want)} of the {len(acts)} messages that match without it. {len(untagged)} of those "
                f"{len(acts)} carry no act tag, and a missing tag says nothing about a message]") in env.text
