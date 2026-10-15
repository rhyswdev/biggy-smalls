"""Slack workspace export: slack/channels.json, slack/users.json and slack/<channel>/<date>.json.
Thread counts (reply_count, reply_users, latest_reply) hold their values at export time (15:30); the
tool layer recomputes them at `now`."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from smalls.contracts.handles import slack_handle
from smalls_data.gen.core import Ctx, content, epoch_s, hexid, rng
from smalls_data.gen.model import Model
from smalls_data.gen.pgsa import pgsa_text

TEAM = "T0LARKSPUR"


class Slack:
    def __init__(self, ctx: Ctx, model: Model):
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.people = {p["handle"]: p for p in ctx.facts["people"]}
        self.bots = {b["name"]: b for b in ctx.facts["bots"]["slack"]}
        self.r = rng(ctx.seed, "slack")
        self.msgs: dict[str, list[dict]] = {}

    def uid(self, handle: str) -> str:
        return self.people[handle]["slack_id"]

    def mentions(self, text: str) -> str:
        return re.sub(r"@([a-z]+\.[a-z]+)", lambda m: f"<@{self.uid(m.group(1))}>" if m.group(1) in self.people else m.group(0), text)

    # -- the pg_stat_activity paste ----------------------------------------------------------------

    def pgsa(self) -> tuple[str, str]:
        return pgsa_text(self.ctx, self.m)

    # -- messages ------------------------------------------------------------------------------------

    def add(self, channel: str, t: datetime, user: str | None, text: str, cls: str, kind: str = "noise", sig_id=None,
            thread_of: str | None = None, subtype: str | None = None, bot: str | None = None, reactions=None, key: str | None = None):
        self.msgs.setdefault(channel, []).append({"t": t, "user": user, "text": text, "cls": cls, "kind": kind, "sig_id": sig_id,
                                                  "thread_of": thread_of, "subtype": subtype, "bot": bot, "reactions": reactions or [],
                                                  "key": key})

    def bot_posts(self) -> None:
        # Datadog posts every notification whose rendered message mentions @slack-sre-oncall, except while the
        # monitor is muted (Sam mutes the redis monitor for an hour at 14:14).
        mons = {m["id"]: m for m in self.ctx.scenario["datadog"]["monitors"]}
        day0 = self.c.t("00:00")
        for t0, ev, meta in self.ctx.datadog_events:
            if t0 < day0 or "@slack-sre-oncall" not in ev["text"]:
                continue
            mon = mons[ev["monitor_id"]]
            mute = mon.get("muted_by")
            if mute and self.c.t(mute["at"]) <= t0 < self.c.t(mute["at"]) + timedelta(hours=1):
                continue
            t = t0 + timedelta(seconds=4)
            text = (f"*{ev['title']}*\n"
                    f"<https://app.datadoghq.com/monitors/{mon['id']}|Monitor {mon['id']}> · `{mon.get('query', '')}`")
            self.add("sre-oncall", t, None, text, "slack.bot.datadog", meta["kind"], None, bot="Datadog")
        # PagerDuty's Slack app posts in the incident channel once it exists.
        pd = {i["number"]: i for i in self.ctx.scenario["pagerduty"]["incidents"]}
        name = lambda h: self.people[h]["name"]
        if "pagerduty_posts" in self.ctx.scenario["slack"]:      # a scenario that lists its own posts
            for p in self.ctx.scenario["slack"]["pagerduty_posts"]:
                text = p["text"].replace("{title}", pd[p["incident"]]["title"]) if p.get("incident") else p["text"]
                for h in self.people:
                    text = text.replace("{" + h + "}", name(h))
                self.add("inc-checkout-latency", self.c.t(p["at"]), None, text, "slack.bot.pagerduty", "signal", None, bot="PagerDuty")
            return
        posts = [("14:35:04", f"Incident #8812 *{pd[8812]['title']}* on Payments - Prod: Triggered, assigned to {name('tom.beckett')}"),
                 ("14:35:05", f"Incident #8814 *{pd[8814]['title']}* on Checkout - Prod: Acknowledged by {name('alice.nguyen')}"),
                 ("14:37:01", "Incident #8815 *[Synthetics] checkout flow* merged into #8814 by " + name("alice.nguyen")),
                 ("14:42:27", f"Incident #8812 escalated to {name('priya.raman')} (no acknowledgement after 10 minutes)"),
                 ("14:44:11", f"Incident #8812 acknowledged by {name('priya.raman')}"),
                 ("15:10:31", f"Incident #8812 resolved by {name('priya.raman')}"),
                 ("15:10:46", f"Incident #8814 resolved by {name('alice.nguyen')}")]
        for at, text in posts:
            self.add("inc-checkout-latency", self.c.t(at), None, text, "slack.bot.pagerduty", "signal", None, bot="PagerDuty")

    def load(self) -> None:
        fixed = content("slack/fixed.yaml")["channels"]
        summary, rows = self.pgsa() if "pgsa_snapshot" in self.ctx.scenario["model"]["connections"] else ("", "")
        for ch, msgs in fixed.items():
            for m in msgs:
                text = m["text"].replace("{pgsa_summary}", summary).replace("{pgsa_rows}", rows)
                if m.get("subtype") == "channel_join":
                    text = text.replace("{self}", self.uid(m["author"]))
                self.add(ch, self.c.t(m["at"]), m["author"], text, m.get("class", "slack.fixed"), m.get("kind", "signal"), m.get("sig_id"),
                         thread_of=m.get("thread_of"), subtype=m.get("subtype"), key=f"{m['at']}/{m['author']}")
        for ref in self.ctx.scenario["slack"]["chatter"]:
            data = content(ref)
            for m in data["messages"]:
                self.add(data["channel"], self.c.t(m["at"]), m["author"], m["text"], "slack.chatter", "noise", None,
                         thread_of=m.get("thread_of"), reactions=m.get("reactions"), key=f"{m['at']}/{m['author']}")
        self.bot_posts()

    def render(self) -> None:
        ctx = self.ctx
        self.load()
        export = self.c.t("15:30")
        chans = {c["name"]: c for c in ctx.scenario["slack"]["channels"]}
        chan_out = []
        for name, ch in chans.items():
            msgs = sorted((m for m in self.msgs.get(name, []) if m["t"] < export), key=lambda m: m["t"])
            seq = {}
            for m in msgs:
                s = epoch_s(m["t"])
                micro = m["t"].microsecond + seq.get(s, 0) * 7 + self.r.randint(1, 6)
                seq[s] = seq.get(s, 0) + 1
                m["ts"] = f"{s}.{micro:06d}"
            by_key = {m["key"]: m for m in msgs if m["key"]}
            out = []
            for m in msgs:
                d: dict = {"type": "message"}
                if m["bot"]:
                    b = self.bots[m["bot"]]
                    d.update({"subtype": "bot_message", "text": m["text"], "ts": m["ts"], "username": m["bot"], "bot_id": b["bot_id"]})
                else:
                    if m["subtype"]:
                        d["subtype"] = m["subtype"]
                    d.update({"user": self.uid(m["user"]), "text": self.mentions(m["text"]), "ts": m["ts"], "team": TEAM})
                    if not m["subtype"]:
                        d["client_msg_id"] = self.uuid(m["ts"])
                if m["thread_of"]:
                    root = by_key[m["thread_of"]]
                    d["thread_ts"] = root["ts"]
                    d["parent_user_id"] = self.uid(root["user"])
                    root.setdefault("replies", []).append(m)
                if m["reactions"]:
                    pool = [p["slack_id"] for p in self.people.values() if p.get("slack") is True and p["handle"] != m["user"]]
                    d["reactions"] = [{"name": x["name"], "count": x["count"], "users": pool[: x["count"]]} for x in m["reactions"]]
                m["out"] = d
                out.append(d)
            for m in msgs:
                if m.get("replies"):
                    reps = m["replies"]
                    users = list(dict.fromkeys(self.uid(x["user"]) for x in reps))
                    m["out"].update({"thread_ts": m["ts"], "reply_count": len(reps), "reply_users_count": len(users),
                                     "latest_reply": reps[-1]["ts"], "reply_users": users})
            rel = f"slack/{name}/{self.c.day.isoformat()}.json"
            ctx.write_json(rel, out)
            for i, m in enumerate(msgs):
                ctx.row(slack_handle(name, m["ts"], m["out"]["text"]), rel, f"/{i}", m["t"], m["cls"], m["kind"], m["sig_id"])
            ctx.count("slack_messages", len(out))
            creator = self.uid(ch["creator"])
            members = [p["slack_id"] for p in self.people.values() if p.get("slack") is True]
            created = self.c.t(ch["created"])
            chan_out.append({"id": "C0" + hexid(rng(ctx.seed, "chan", name), 9).upper(), "name": name, "created": epoch_s(created),
                             "creator": creator, "is_archived": False, "is_general": False, "members": members,
                             "topic": {"value": "", "creator": "", "last_set": 0},
                             "purpose": {"value": ch["purpose"], "creator": creator, "last_set": epoch_s(created)}})
        ctx.write_json("slack/channels.json", chan_out)
        users = []
        for p in self.ctx.facts["people"]:
            if not p.get("slack"):
                continue
            off = int(ZoneInfo(p["tz"]).utcoffset(self.c.t("12:00").replace(tzinfo=None)).total_seconds())
            users.append({"id": p["slack_id"], "team_id": TEAM, "name": p["handle"], "deleted": p["slack"] == "deactivated",
                          "real_name": p["name"], "tz": p["tz"], "tz_label": self.tz_label(p["tz"]), "tz_offset": off,
                          "profile": {"title": p.get("title", ""), "real_name": p["name"], "display_name": p["display_name"],
                                      "real_name_normalized": p["name"], "display_name_normalized": p["display_name"],
                                      "email": f"{p['handle']}@larkspur.com", "team": TEAM},
                          "is_admin": False, "is_owner": False, "is_bot": False, "is_app_user": False, "updated": 1789000000})
        for b in self.ctx.facts["bots"]["slack"]:
            users.append({"id": b["app_user"], "team_id": TEAM, "name": b["name"].lower(), "deleted": False, "real_name": b["name"],
                          "profile": {"real_name": b["name"], "display_name": "", "bot_id": b["bot_id"], "team": TEAM},
                          "is_admin": False, "is_owner": False, "is_bot": True, "is_app_user": False, "updated": 1760000000})
        ctx.write_json("slack/users.json", users)

    def tz_label(self, tz: str) -> str:
        return {"Europe/London": "British Summer Time", "Europe/Rome": "Central European Summer Time",
                "America/New_York": "Eastern Daylight Time"}.get(tz, tz)

    def uuid(self, key: str) -> str:
        h = hexid(rng(self.ctx.seed, "cmid", key), 32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
