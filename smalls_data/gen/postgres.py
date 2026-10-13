"""pay-db-01 PostgreSQL 15 log: splunk/raw/postgres/pay-db-01.log, log_line_prefix '%m [%p] %u@%d ',
log_connections=on. Connection events follow the connection budget in the scenario YAML.

Every backend the server starts takes its pid from one counter that rises with time (data fix 7): logged
and unlogged connections, refused attempts and autovacuum workers alike. The renderer tracks which
backends are live, so the 14:56 kill terminates live gateway sessions, the EOF lines at the 3.7.9
restarts close sessions that are still open, and the DBA's pg_stat_activity paste quotes real pids."""

from __future__ import annotations

import heapq
from collections import deque
from datetime import datetime, timedelta

from smalls_data.gen.core import Ctx, Rec, every_nth, rng
from smalls_data.gen.model import HOSTS, Model
from smalls_data.lint_schema import exact_strings

KIND = {"pg.connect": "noise", "pg.gw_connect": "signal", "pg.recon_connect": "signal", "pg.fatal_53300": "signal",
        "pg.terminate_admin": "signal", "pg.eof_open_txn": "signal", "pg.checkpoint": "noise", "pg.autovacuum": "noise",
        "pg.dba_connect": "signal"}


class Pids:
    """Backend pids: one counter, advanced by every backend the server starts and by short-lived backends
    the log does not show (about one every 20 s)."""

    def __init__(self, start: int, t0: datetime, r):
        self.value, self.t, self.r = start, t0, r

    def next(self, t: datetime) -> int:
        if t < self.t:
            raise ValueError("pids are allocated in time order")
        self.value += int((t - self.t).total_seconds() / 20) + self.r.randint(1, 3)
        self.t = t
        return self.value


class Postgres:
    def __init__(self, ctx: Ctx, model: Model):
        ctx.per_unit.update({c: 2 for c in ("pg.connect", "pg.gw_connect", "pg.recon_connect", "pg.dba_connect", "pg.fatal_53300")})
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.folder = "splunk/raw/postgres"
        self.shape = ctx.scenario["log_shapes"]["postgres"]
        self.sig = {s["id"]: s for s in ctx.scenario["postgres_signals"]}
        self.net = ctx.facts["network"]
        self.gw = ctx.facts["parameters"]["payments_gateway"]
        self.recs: list[Rec] = []
        self.cnt: dict = {}
        self.r = rng(ctx.seed, "pg")
        self.win = [(self.c.t(a), self.c.t(b)) for a, b in ctx.scenario["log_windows"]["postgres"]]
        self.fatal = exact_strings()["pg.fatal_53300"]
        self.events: list[tuple[datetime, int, int, str, dict]] = []    # a heap: (t, priority, seq, kind, detail)
        self.seq = 0
        self.live: dict[str, deque] = {h: deque() for h in HOSTS}   # live gateway backends per host, oldest first

    def ts(self, t: datetime) -> str:
        return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d} UTC"

    def in_window(self, t: datetime) -> bool:
        return any(a <= t < b for a, b in self.win)

    def pop(self, t, cls, n=1):
        if self.in_window(t):
            self.ctx.pop(cls, t, n)

    def add(self, t, pid, user, db, level, msg, cls, sig_id=None, order=0):
        self.pop(t, cls)
        text = f"{self.ts(t)} [{pid}] {user}@{db} {level}:  {msg}"
        self.recs.append(Rec(t=t, text=text, cls=cls, kind=KIND[cls], sig_id=sig_id, order=order))

    def event(self, t: datetime, kind: str, prio: int = 5, **kw) -> None:
        self.seq += 1
        heapq.heappush(self.events, (t, prio, self.seq, kind, kw))

    # -- what happens, in time order ---------------------------------------------------------------

    def plan(self) -> None:
        S, gw, c = self.shape, self.gw, self.c
        hik = gw["hikari"]
        osvc = self.ctx.facts["parameters"]["order_service"]
        # Before the window: the Datadog agent's connection, each gateway pool as it stood after the 3.8.0
        # restarts, and the order-service connections live at 13:45.
        self.event(c.t("2026-10-12T22:14:07Z"), "connect", pool=("dd",), who="datadog")
        for h in HOSTS:
            t = self.m.v380_at[h] - timedelta(seconds=7.9)
            for j in range(hik["minimumIdle"]):
                self.event(t + timedelta(milliseconds=40 + j * 23), "connect", pool=("gw", h), who="gw")
            self.event(self.m.v380_at[h] + timedelta(minutes=self.r.randint(20, 90)), "connect", pool=("gw", h), who="gw")
        # Gateway pool growth by budget row; shrink on idleTimeout outside the restarts.
        rows = self.m.conn_rows
        for (t0, r0), (t1, r1) in zip(rows, rows[1:]):
            if (t1 - t0).total_seconds() > 3600:
                continue
            for k, h in enumerate(HOSTS):
                d = r1["gateway"][k] - r0["gateway"][k]
                restart = t0 <= self.m.v379_at[h] < t1 + timedelta(minutes=1)
                for j in range(max(0, d)):
                    self.event(t1 + timedelta(milliseconds=int((j + 0.5) * 60000 / d)), "connect", pool=("gw", h), who="gw")
                if d < 0 and not restart:
                    for _ in range(-d):
                        self.event(t1 + timedelta(seconds=self.r.randint(1, 50)), "close", pool=("gw", h))
        # Gateway restarts on 3.7.9: Hikari aborts its in-use connections (EOF with an open transaction), then
        # opens minimumIdle new ones.
        for h in HOSTS:
            self.event(self.m.v379_at[h] - timedelta(seconds=33.9), "restart", prio=1, host=h)
            t = self.m.v379_at[h] - timedelta(seconds=7.9)     # Hikari's "Added connection" at startup
            for j in range(hik["minimumIdle"]):
                self.event(t + timedelta(milliseconds=40 + j * 23), "connect", pool=("gw", h), who="gw")
        # order-service: fixed pools reconnect on maxLifetime (30 min), every one logged.
        for w0, w1 in self.win:
            for i in range(osvc["total"]):
                h = osvc["instances"][i % 2]
                t = w0 + timedelta(seconds=self.r.randint(0, 1799))
                self.event(t - timedelta(seconds=1800), "connect", pool=("order", i), who="order", host=h)
                while t < w1:
                    self.event(t, "connect", pool=("order", i), who="order", host=h)
                    t += timedelta(seconds=1800)
        # The reconciler's 40 connections at 14:30, held until it finishes.
        rc = self.sig["recon_connect"]
        t = c.t(rc["at"])
        for i in range(rc["count"]):
            self.event(t, "connect", pool=("recon",), who="recon", sig=rc["sig_id"] if i == 0 else None)
            t += timedelta(milliseconds=self.r.randint(*rc["spacing_ms"]))
        # The Datadog agent loses its slot at 14:34 (budget "other" 1 to 0) and gets one back at 15:08.
        self.event(c.t("14:33:58.600"), "close", pool=("dd",))
        self.event(c.t("15:08:00") + timedelta(milliseconds=self.r.randint(100, 900)), "connect", pool=("dd",), who="datadog")
        # 53300 refusals: pay-04's pool growth every 10 s and the Datadog agent every 15 s, from 14:33:02 until
        # pay-04 restarts and slots free up. Every attempt starts a backend; one session in keep_1_in is logged.
        f = self.sig["first_53300"]
        t0 = c.t(f["at"])
        t = t0
        while t < self.m.v379_at["pay-04"] - timedelta(seconds=36):
            self.event(t, "refuse", who="pay-04", sig=f["sig_id"] if t == t0 else None)
            t += timedelta(seconds=10)
        t = t0 + timedelta(milliseconds=412)
        while t < c.t("15:08"):
            self.event(t, "refuse", who="datadog")
            t += timedelta(seconds=15)
        # The DBA's psql session on a reserved superuser slot, open from 14:34 through the 14:56 kill.
        self.event(c.t(S["dba_connect_at"]), "connect", pool=("dba",), who="dba")
        self.event(c.t(self.ctx.scenario["model"]["connections"]["pgsa_snapshot"]["at"]), "paste", prio=9)
        ks = self.sig["kill_sessions"]
        self.event(c.t(ks["at"]), "kill", prio=2, count=ks["count"], sig=ks["sig_id"])
        for w0, w1 in self.win:
            for tbl in S["autovacuum_tables"]:
                self.event(w0 + timedelta(seconds=self.r.randint(60, int((w1 - w0).total_seconds()) - 60)), "autovacuum", table=tbl)

    def connect(self, t, pid, host_ip, user, db, app, cls, sig_id=None):
        self.add(t, pid, "[unknown]", "[unknown]", "LOG", f"connection received: host={host_ip} port={self.r.randint(33000, 60999)}", cls, order=0)
        self.add(t + timedelta(milliseconds=self.r.randint(2, 6)), pid, user, db, "LOG",
                 f"connection authorized: user={user} database={db} application_name={app}", cls, sig_id, order=1)

    def refused(self, t, pid, host_ip, user, db, sig_id=None):
        self.add(t - timedelta(milliseconds=1), pid, "[unknown]", "[unknown]", "LOG",
                 f"connection received: host={host_ip} port={self.r.randint(33000, 60999)}", "pg.fatal_53300", order=0)
        self.add(t, pid, user, db, "FATAL", self.fatal, "pg.fatal_53300", sig_id, order=1)

    def paste(self) -> None:
        """The pids of the five oldest idle-in-transaction sessions in the DBA's paste: live gateway backends
        on the hosts the paste lists (smalls_data/gen/slack.py)."""
        r = rng(self.ctx.seed, "pgsa")
        out, used = [], set()
        for h in ["pay-02", "pay-01", "pay-03", "pay-02", "pay-04"]:
            pid = r.choice([p for p in self.live[h] if p not in used])
            used.add(pid)
            out.append((pid, h))
        self.m.pgsa_pids = out

    def render(self) -> None:
        self.plan()
        gw = self.gw
        osvc = self.ctx.facts["parameters"]["order_service"]
        rp = self.ctx.facts["parameters"]["payments_reconciler"]
        pids = Pids(31842, self.c.t("2026-10-12T22:00:00Z"), self.r)
        n_refused = self.ctx.keep_1_in(self.folder, "pg.fatal_53300")
        n_eof = self.ctx.keep_1_in(self.folder, "pg.eof_open_txn")
        refused = 0
        while self.events:
            t, _, _, kind, kw = heapq.heappop(self.events)
            if kind == "connect":
                pid = pids.next(t)
                pool = kw["pool"]
                if pool[0] == "gw":
                    self.live[pool[1]].append(pid)
                    self.connect(t, pid, self.net[pool[1]], gw["db_user"], "payments", gw["application_name"], "pg.gw_connect")
                elif pool[0] == "order":
                    self.connect(t, pid, self.net[kw["host"]], osvc["db_user"], "orders", osvc["application_name"], "pg.connect")
                elif pool[0] == "recon":
                    self.connect(t, pid, self.net[self.sig["recon_connect"]["client"]], rp["db_user"], "payments",
                                 rp["application_name"], "pg.recon_connect", kw.get("sig"))
                elif pool[0] == "dba":
                    self.connect(t, pid, self.net["bastion"], "postgres", "payments", "psql", "pg.dba_connect")
                elif pool[0] == "dd":
                    self.connect(t, pid, self.net["pay-db-01"], "datadog", "postgres", "datadog-agent", "pg.connect")
            elif kind == "close":
                if kw["pool"][0] == "gw" and self.live[kw["pool"][1]]:
                    self.live[kw["pool"][1]].popleft()
            elif kind == "refuse":
                pid = pids.next(t)
                if refused % n_refused == 0:
                    if kw["who"] == "pay-04":
                        self.refused(t, pid, self.net["pay-04"], gw["db_user"], "payments", kw.get("sig"))
                    else:
                        self.refused(t, pid, self.net["pay-db-01"], "datadog", "postgres")
                else:
                    self.pop(t, "pg.fatal_53300", 2)
                refused += 1
            elif kind == "restart":
                h = kw["host"]
                while self.live[h]:
                    pid = self.live[h].popleft()
                    tt = t + timedelta(milliseconds=self.r.randint(0, 400))
                    if every_nth(self.cnt, ("eof",), n_eof):
                        self.add(tt, pid, gw["db_user"], "payments", "LOG",
                                 "unexpected EOF on client connection with an open transaction", "pg.eof_open_txn")
                    else:
                        self.pop(tt, "pg.eof_open_txn")
            elif kind == "kill":
                # pg_terminate_backend over idle-in-transaction gateway sessions; each pool refills at once.
                live = [(pid, h) for h in HOSTS for pid in self.live[h]]
                victims = sorted(rng(self.ctx.seed, "kill").sample(live, kw["count"]))
                for i, (pid, h) in enumerate(victims):
                    self.live[h].remove(pid)
                    self.add(t + timedelta(milliseconds=50 + i * 3), pid, gw["db_user"], "payments", "FATAL",
                             "terminating connection due to administrator command", "pg.terminate_admin", kw["sig"] if i == 0 else None)
                for _, h in victims:
                    self.event(t + timedelta(milliseconds=200 + self.r.randint(0, 3800)), "connect", pool=("gw", h), who="gw")
            elif kind == "paste":
                self.paste()
            elif kind == "autovacuum":
                self.add(t, pids.next(t), "", "", "LOG", f'automatic analyze of table "{kw["table"]}"', "pg.autovacuum")
        self.checkpoints()
        recs = [r for r in self.recs if self.in_window(r.t)]
        self.ctx.write_log("splunk/raw/postgres/pay-db-01.log", recs, "log_lines:splunk/raw/postgres")

    def checkpoints(self) -> None:
        """The checkpointer is a long-lived child of the postmaster: a low, fixed pid."""
        S = self.shape
        for w0, w1 in self.win:
            t = w0 + timedelta(milliseconds=self.r.randint(10000, 300000))
            while t < w1:
                self.add(t, 1021, "", "", "LOG", "checkpoint starting: time", "pg.checkpoint")
                b = self.r.randint(900, 4200)
                w = round(b / 52.0, 3)
                self.add(t + timedelta(seconds=w), 1021, "", "", "LOG",
                         f"checkpoint complete: wrote {b} buffers ({b / 1310.72:.1f}%); 0 WAL file(s) added, 0 removed, "
                         f"{self.r.randint(1, 4)} recycled; write={w:.3f} s, sync=0.0{self.r.randint(10, 99)} s, total={w + 0.1:.3f} s; "
                         f"sync files={self.r.randint(20, 60)}, longest=0.00{self.r.randint(2, 9)} s, average=0.001 s; "
                         f"distance={b * 8} kB, estimate={b * 8 + self.r.randint(100, 900)} kB", "pg.checkpoint")
                t += timedelta(seconds=S["checkpoint_every_s"])
