"""payments-gateway application logs: splunk/raw/payments_app/pay-0N.log (custom Logback pattern,
server clock UTC, no zone; %ex{20} per throwable). Sampled from the model's request stream."""

from __future__ import annotations

from datetime import datetime, timedelta

from smalls_data.gen.core import Ctx, Rec, abbreviate_logger, content, every_nth, hexid, parse_rate, rng
from smalls_data.gen.model import HOSTS, Model, Req

KIND = {  # class -> kind (noise classes are listed with their tag in the scenario YAML noise inventory)
    "log.startup": "noise", "log.shutdown": "noise", "log.startup_config": "signal",
    "log.request_ok": "noise", "log.psp_ok_slow": "signal", "log.psp_retry": "signal",
    "log.psp_gave_up": "signal", "log.psp_fail_fast": "signal", "log.hikari_stats": "noise",
    "log.hikari_stats_full": "signal", "log.hikari_added": "signal", "log.hikari_timeout": "signal",
    "log.hikari_leak": "signal", "log.hikari_unleaked": "signal", "log.hikari_cannot_acquire": "signal",
    "log.health": "signal", "log.bg_scheduler": "noise",
}


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S,") + f"{dt.microsecond // 1000:03d}"


class Gateway:
    def __init__(self, ctx: Ctx, model: Model):
        ctx.per_unit.update({"log.psp_retry": 3, "log.hikari_timeout": 3})
        self.ctx, self.m = ctx, model
        self.c = ctx.clock
        self.tpl = content("logs/payments.yaml")
        self.folder = "splunk/raw/payments_app"
        self.shape = ctx.scenario["log_shapes"]
        self.sig = {s["id"]: s for s in ctx.scenario["gateway_signals"]}
        self.recs: dict[str, list[Rec]] = {h: [] for h in HOSTS}
        self.cnt: dict = {}
        self.conn_ids: dict[str, list[str]] = {}

    # -- formatting -----------------------------------------------------------------------------

    def line(self, at: datetime, spec, th: str | None = None, **kw) -> str:
        level, thread_key, logger_key, msg = spec
        th = th or self.tpl["threads"][thread_key].format(n=kw.get("n", 1))
        logger = abbreviate_logger(self.tpl["loggers"][logger_key])
        return f"{ts(at)} {level:<5} [{th}] {logger} - {msg.format(**kw)}"

    def add(self, host: str, t: datetime, text: str, cls: str, sig_id: str | None = None, order: int = 0, key=None) -> None:
        if self.in_window(t):
            self.ctx.pop(cls, t, key=key)
        self.recs[host].append(Rec(t=t, text=text, cls=cls, kind=KIND[cls], sig_id=sig_id, order=order))

    def drop(self, t: datetime, cls: str, n: int = 1, key=None) -> None:
        """A record the estate wrote and the sample did not keep."""
        if self.in_window(t):
            self.ctx.pop(cls, t, n, key=key)

    def rate(self, cls: str) -> int:
        return self.ctx.keep_1_in(self.folder, cls)

    def conn(self, host: str, i: int) -> str:
        ids = self.conn_ids.setdefault(host, [])
        r = rng(self.ctx.seed, "conn", host)
        while len(ids) <= i:
            ids.append(hexid(r, 8).lstrip("0") or "1")
        return ids[i]

    def in_window(self, t: datetime) -> bool:
        return any(a <= t < b for a, b in self.m.windows())

    # -- renderers ------------------------------------------------------------------------------

    def startups(self) -> None:
        gw = self.ctx.facts["parameters"]["payments_gateway"]
        for sid in ("startup_380", "startup_379"):
            s = self.sig[sid]
            v = s["version"]
            cfg = gw["versions"][v]
            for host, at in s["hosts"].items():
                r = rng(self.ctx.seed, "pid", host, v)
                T = self.c.t(at) + timedelta(milliseconds=r.randint(101, 899))
                start0 = T - timedelta(seconds=30) - timedelta(milliseconds=r.randint(5, 60))
                pid = r.randint(1800, 9800) if v == "3.8.0" else r.randint(21000, 29000)
                offs = [-6.0, -4.1, -3.9, -3.7]
                for o, spec in zip(offs, self.tpl["shutdown"]):
                    t = start0 + timedelta(seconds=o)
                    self.add(host, t, self.line(t, spec), "log.shutdown", order=-10 + len(offs))
                offs = [1.9, 2.0, 14.6, 22.1, 22.4, 22.5, 30.0, 42.8, 43.1]
                kw = dict(version=v, pid=pid, conn=self.conn(host, 0 if v == "3.8.0" else 100),
                          timeout_ms=cfg["psp_timeout_ms"], retries=cfg["psp_retries"],
                          backoff=",".join(map(str, cfg["backoff_ms"])) or "none",
                          started_in=gw["startup"]["started_in_s"], running_for=gw["startup"]["process_running_s"])
                for k, (o, spec) in enumerate(zip(offs, self.tpl["startup"])):
                    t = start0 + timedelta(seconds=o)
                    if "PSP client configured" in spec[3]:
                        t = T
                        self.add(host, t, self.line(t, spec, **kw), "log.startup_config", s["sig_id"], order=k)
                    else:
                        self.add(host, t, self.line(t, spec, **kw), "log.startup", order=k)

    def request_lines(self) -> None:
        """Request-path lines, kept per cart at one constant rate per class (sampling: in the scenario)."""
        n_ok, n_slow, n_retry = self.rate("log.request_ok"), self.rate("log.psp_ok_slow"), self.rate("log.psp_retry")
        n_ff, n_to, n_leak = self.rate("log.psp_fail_fast"), self.rate("log.hikari_timeout"), self.rate("log.hikari_leak")
        traced_hosts: set[str] = set()
        first_leak = self.sig["first_leak"]
        for q in self.m.requests():
            if not self.in_window(q.t):
                continue
            h = q.host
            th = f"http-nio-8443-exec-{q.thread}"
            if q.outcome == "ok":
                t = q.borrow_t + timedelta(milliseconds=q.gw_ms)
                if not q.kept(n_ok):
                    self.drop(t, "log.request_ok", key=q.cart)
                    continue
                amount = rng(self.ctx.seed, "amt", q.order).randint(800, 26000) / 100
                self.add(h, t, self.line(t, self.tpl["request_ok"], th=th, order=q.order, amount=f"{amount:.2f}",
                                         currency="EUR" if q.eu else "USD", psp_ms=q.psp_ms, attempts=1), "log.request_ok", key=q.cart)
            elif q.outcome == "slow_ok":
                t = q.borrow_t + timedelta(milliseconds=q.psp_ms + 3)
                if not q.kept(n_slow):
                    self.drop(t, "log.psp_ok_slow", key=q.cart)
                    continue
                self.add(h, t, self.line(t, self.tpl["psp_ok_slow"], th=th, psp_ms=q.psp_ms, order=q.order), "log.psp_ok_slow", key=q.cart)
            elif q.outcome == "gave_up":
                anchor = q.anchor == self.sig["first_retry"]["id"]
                self.retry_lines(q, th, anchor, keep=q.kept(n_retry))
                # Leak detection fires 30 s after the borrow; the connection returns when the request ends.
                t_leak = q.borrow_t + timedelta(seconds=30)
                keep = q.kept(n_leak) and (anchor or h != first_leak["host"] or t_leak > self.c.t(first_leak["at"]))
                if keep:
                    self.cnt[("leak", h)] = self.cnt.get(("leak", h), 0) + 1
                    self.leak_lines(q, th, anchor)
                else:
                    self.drop(t_leak, "log.hikari_leak", key=q.cart)
                    self.drop(q.borrow_t + timedelta(milliseconds=q.gw_ms), "log.hikari_unleaked", key=q.cart)
            elif q.outcome == "pool_timeout":
                t = q.t + timedelta(milliseconds=q.gw_ms)
                if not q.kept(n_to):
                    self.drop(t, "log.hikari_timeout", 3, key=q.cart)
                    continue
                first = q.anchor == self.m.first_pt["id"]
                traced = h not in traced_hosts
                traced_hosts.add(h)
                self.timeout_lines(h, t, th, q, traced=traced, sig=self.m.first_pt["sig_id"] if first else None,
                                   stats=self.m.first_pt["stats"] if first else None)
            elif q.outcome == "fail_fast":
                t = q.borrow_t + timedelta(milliseconds=q.gw_ms)
                if not q.kept(n_ff):
                    self.drop(t, "log.psp_fail_fast", key=q.cart)
                    continue
                self.add(h, t, self.line(t, self.tpl["psp_fail_fast"], th=th, order=q.order, elapsed_ms=q.gw_ms - 8,
                                         timeout_ms=q.psp_ms), "log.psp_fail_fast", key=q.cart)

    def retry_lines(self, q: Req, th: str, anchor: bool, keep: bool = True) -> None:
        cfg = self.m.versions[q.version]
        t = q.borrow_t
        attempts = cfg["psp_retries"] + 1
        for a in range(1, attempts + 1):
            t = t + timedelta(milliseconds=cfg["psp_timeout_ms"] + (13 if a == 1 else 4))
            if a < attempts:
                back = cfg["backoff_ms"][a - 1]
                if keep:
                    sig = self.sig["first_retry"]["sig_id"] if anchor and a == 1 else None
                    self.add(q.host, t, self.line(t, self.tpl["psp_retry"], th=th, attempt=a, max_attempts=attempts,
                                                  timeout_ms=cfg["psp_timeout_ms"], order=q.order, backoff_ms=back),
                             "log.psp_retry", sig, key=q.cart)
                else:
                    self.drop(t, "log.psp_retry", key=q.cart)
                t = t + timedelta(milliseconds=back)
            elif keep:
                self.add(q.host, t, self.line(t, self.tpl["psp_gave_up"], th=th, order=q.order, max_attempts=attempts,
                                              elapsed_ms=int((t - q.borrow_t).total_seconds() * 1000)), "log.psp_gave_up", key=q.cart)
            else:
                self.drop(t, "log.psp_gave_up", key=q.cart)

    def leak_lines(self, q: Req, th: str, anchor: bool) -> None:
        L = self.tpl["hikari_leak"]
        fl = self.sig["first_leak"]
        conn = fl["connection"].split("@")[1] if anchor else self.conn(q.host, 200 + self.cnt[("leak", q.host)])
        t = q.borrow_t + timedelta(seconds=30)
        text = "\n".join([self.line(t, L["head"], conn=conn, thread=th)] + L["trace"])
        self.add(q.host, t, text, "log.hikari_leak", fl["sig_id"] if anchor else None, key=q.cart)
        t2 = q.borrow_t + timedelta(milliseconds=q.gw_ms)
        self.add(q.host, t2, self.line(t2, L["unleaked"], th=th, conn=conn, thread=th), "log.hikari_unleaked", key=q.cart)

    def timeout_lines(self, h: str, t: datetime, th: str, q: Req, traced: bool, sig=None, stats=None) -> None:
        T = self.tpl["hikari_timeout"]
        tt, a, i, w = (stats["total"], stats["active"], stats["idle"], stats["waiting"]) if stats else self.m.stats(t, h)
        kw = dict(t=tt, a=a, i=i, w=w, order=q.order)
        self.add(h, t, self.line(t, T["warn"], th=th, **kw), "log.hikari_timeout", order=0, key=q.cart)
        self.add(h, t, self.line(t, T["error"], th=th, **kw), "log.hikari_timeout", sig, order=1, key=q.cart)
        t2 = t + timedelta(milliseconds=2)
        if traced:
            text = "\n".join([self.line(t2, T["handler"], th=th, **kw)] + [x.format(**kw) for x in T["trace"]])
        else:
            text = self.line(t2, T["oneline_handler"], th=th, **kw)
        self.add(h, t2, text, "log.hikari_timeout", order=2, key=q.cart)

    def housekeeper(self) -> None:
        """The housekeeper logs every 30 s per host; one run in `keep_1_in` is kept, on a fixed grid per host
        that puts the anchored 14:31:34 line on pay-02 on the grid."""
        every = self.shape["hikari_stats"]["every_s"]
        n = self.rate("log.hikari_stats")
        full = self.sig["stats_full"]
        full_t = self.c.t(full["at"])
        for h in HOSTS:
            phase = 4.009 if h == full["host"] else rng(self.ctx.seed, "hk", h).randint(0, 29999) / 1000
            ref = full_t if h == full["host"] else self.c.t("00:00") + timedelta(seconds=phase)
            for w0, w1 in self.m.windows():
                t = w0.replace(second=0) + timedelta(seconds=phase)
                while t < w1:
                    if not self.restarting(h, t) and self.in_window(t):
                        tt, a, i, wt = self.m.stats(t, h)
                        cls = "log.hikari_stats_full" if (i == 0 and wt > 0) else "log.hikari_stats"
                        if round((t - ref).total_seconds() / every) % n != 0:
                            self.drop(t, cls)
                        else:
                            sig = None
                            if h == full["host"] and abs((t - full_t).total_seconds()) < 0.001:
                                st = full["stats"]
                                tt, a, i, wt = st["total"], st["active"], st["idle"], st["waiting"]
                                cls, sig = "log.hikari_stats_full", full["sig_id"]
                            self.add(h, t, self.line(t, self.tpl["hikari_stats"], t=tt, a=a, i=i, w=wt), cls, sig)
                    t += timedelta(seconds=every)

    def restarting(self, h: str, t: datetime) -> bool:
        for at in (self.m.v380_at[h], self.m.v379_at[h]):
            if at - timedelta(seconds=36) <= t < at - timedelta(seconds=7.5):
                return True
        return False

    def pool_growth(self) -> None:
        n = self.rate("log.hikari_added")
        rows = self.m.conn_rows
        for h in HOSTS:
            k = HOSTS.index(h)
            for (t0, r0), (t1, r1) in zip(rows, rows[1:]):
                grow = r1["gateway"][k] - r0["gateway"][k]
                if grow <= 0 or (t1 - t0).total_seconds() > 3600:
                    continue
                for j in range(grow):
                    t = t1 + timedelta(milliseconds=int((j + 0.5) * 60000 / grow))
                    if every_nth(self.cnt, ("added", h), n):
                        self.add(h, t, self.line(t, self.tpl["hikari_added"], conn=self.conn(h, 1 + r0["gateway"][k] + j)), "log.hikari_added")
                    else:
                        self.drop(t, "log.hikari_added")

    def cannot_acquire(self) -> None:
        """pay-04's connection adder retries every 10 s while the database refuses it; the anchored first
        attempt and then one in `keep_1_in` are kept."""
        every = self.shape["cannot_acquire"]["every_s"]
        n = self.rate("log.hikari_cannot_acquire")
        sig = self.sig["cannot_acquire_first"]
        h, t0 = sig["host"], self.c.t(sig["at"])
        A = self.tpl["hikari_cannot_acquire"]
        t, k = t0, 0
        while t < self.m.v379_at[h] - timedelta(seconds=40):
            if k % n == 0:
                text = "\n".join([self.line(t, A["head"])] + A["trace"])
                self.add(h, t, text, "log.hikari_cannot_acquire", sig["sig_id"] if k == 0 else None)
            else:
                self.drop(t, "log.hikari_cannot_acquire")
            t += timedelta(seconds=every)
            k += 1

    def health(self) -> None:
        n = self.rate("log.health")
        spec = self.tpl["health_fail"]
        for h in HOSTS:
            r = rng(self.ctx.seed, "health", h)
            t = self.c.t("14:30:00") + timedelta(milliseconds=r.randint(0, 4999))
            while t < self.c.t("15:06:00"):
                if self.m.saturated(t, h) and self.m.version(t, h) == "3.8.0" and t >= self.c.t("14:36:00") and r.random() < 0.55:
                    if every_nth(self.cnt, ("health",), n):
                        self.add(h, t, self.line(t, spec, th=f"http-nio-8443-exec-{r.randint(1, 200)}", reason=self.tpl["health_reasons"][0]), "log.health")
                    else:
                        self.drop(t, "log.health")
                t += timedelta(seconds=5)

    def background(self) -> None:
        S = self.shape["gateway_background"]
        for h in HOSTS:
            r = rng(self.ctx.seed, "bg", h)
            for w0, w1 in self.m.windows():
                t = w0 + timedelta(seconds=r.randint(5, 290))
                while t < w1:
                    if not self.restarting(h, t):
                        self.add(h, t, self.line(t, self.tpl["bg_settlement"], th=f"scheduling-{r.randint(1, 4)}", n=r.randint(40, 260)), "log.bg_scheduler")
                    t += timedelta(seconds=S["settlement_every_s"])
                t = w0 + timedelta(seconds=r.randint(60, 1700))
                while t < w1:
                    self.add(h, t, self.line(t, self.tpl["bg_token"], th=f"scheduling-{r.randint(1, 4)}"), "log.bg_scheduler")
                    t += timedelta(seconds=S["token_every_s"])

    def render(self) -> None:
        self.startups()
        self.request_lines()
        self.housekeeper()
        self.pool_growth()
        self.cannot_acquire()
        self.health()
        self.background()
        self.truncate_one()
        for h in HOSTS:
            recs = [r for r in self.recs[h] if self.in_window(r.t)]
            self.ctx.write_log(f"splunk/raw/payments_app/{h}.log", recs, "log_lines:splunk/raw/payments_app")

    def truncate_one(self) -> None:
        """The deliberate defect (PLAN 2): one leak trace on pay-03 cut mid-frame where the forwarder hit
        its line-length limit; the next line is a bare `at ...` frame with no leading tab."""
        d = self.ctx.scenario["defects"]["truncated_line"]
        near = self.c.t(d["near"])
        leaks = [r for r in self.recs[d["host"]] if r.cls == "log.hikari_leak"]
        if not leaks:
            return
        rec = min(leaks, key=lambda r: abs((r.t - near).total_seconds()))
        lines = rec.text.split("\n")
        k = next(i for i, x in enumerate(lines) if "TransactionDriverControlImpl" in x)
        cut = lines[k][:118]
        lines[k:k + 2] = [cut, lines[k + 1].lstrip("\t")]
        rec.text = "\n".join(lines)
