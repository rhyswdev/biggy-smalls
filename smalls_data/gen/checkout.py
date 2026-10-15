"""checkout-api logs: splunk/raw/checkout_api/<pod>.jsonl (logstash-logback-encoder JSON, @timestamp in
UTC with Z; kubernetes.* added by the shipper). Gateway calls come from the model's request stream."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from smalls_data.gen.core import Ctx, Rec, content, every_nth, hexid, iso_z, parse_rate, rng
from smalls_data.gen.model import Model

KIND = {"log.co_request": "noise", "log.co_gw_timeout": "signal", "log.co_gw_5xx": "signal",
        "log.co_debug_flood": "trap", "log.co_startup": "noise", "log.co_shutdown": "noise", "log.co_bg": "noise",
        "log.co_db_timeout": "signal", "log.co_audit_slow": "signal"}
PER_CART = ("log.co_gw_timeout", "log.co_gw_5xx", "log.co_db_timeout")     # request-path classes kept per cart
LEVEL_VALUE = {"DEBUG": 10000, "INFO": 20000, "WARN": 30000, "ERROR": 40000}


class Checkout:
    def __init__(self, ctx: Ctx, model: Model):
        ctx.per_unit.update({"log.co_gw_timeout": 2, "log.co_gw_5xx": 2})
        if model.co:
            ctx.per_unit["log.co_db_timeout"] = 4
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.tpl = content("logs/checkout.yaml")
        self.folder = "splunk/raw/checkout_api"
        self.shape = ctx.scenario["log_shapes"]
        self.pods = ctx.scenario["estate"]["checkout_pods"]
        self.params = ctx.facts["parameters"]["checkout_api"]
        self.win = [(self.c.t(a), self.c.t(b)) for a, b in ctx.scenario["log_windows"]["checkout_api"]]
        self.recs: dict[str, list[Rec]] = {}
        self.cnt: dict = {}
        self.sig = {s["id"]: s for s in ctx.scenario["checkout_signals"]}
        nodes = self.pods["nodes"]
        allpods = self.pods["initial"] + self.pods["rollout_2_14_3"] + self.pods["rollback_2_14_2"]
        self.node = {p: nodes[i % len(nodes)] for i, p in enumerate(allpods)}

    def in_window(self, t: datetime) -> bool:
        return any(a <= t < b for a, b in self.win)

    def version_of(self, pod: str) -> str:
        return "2.14.3" if pod.startswith(self.pods["replicaset_2_14_3"]) else "2.14.2"

    def add(self, pod: str, t: datetime, level: str, logger: str, msg: str, cls: str, thread: str, sig_id=None,
            order_id=None, cart=None, trace=True, stack=None) -> None:
        if self.in_window(t):
            self.ctx.pop(cls, t, key=cart if cls in PER_CART else None)
        r = rng(self.ctx.seed, "trace", order_id) if order_id else rng(self.ctx.seed, "co", pod, t.isoformat(), msg[:20])
        d = {"@timestamp": iso_z(t), "@version": "1", "message": msg, "logger_name": logger, "thread_name": thread,
             "level": level, "level_value": LEVEL_VALUE[level]}
        if stack:
            d["stack_trace"] = stack
        if trace:
            d["traceId"] = hexid(r, 32)
            d["spanId"] = hexid(r, 16)
        if order_id:
            d["orderId"] = order_id
        if cart:
            d["cartId"] = str(cart)
        d["service"] = "checkout-api"
        d["version"] = self.version_of(pod)
        rs = pod.rsplit("-", 1)[0]
        d["kubernetes"] = {"namespace": self.params["namespace"], "pod_name": pod, "container_name": "checkout-api",
                           "node_name": self.node[pod], "labels": {"app": "checkout-api", "pod-template-hash": rs.rsplit("-", 1)[1]}}
        self.recs.setdefault(pod, []).append(Rec(t=t, text=json.dumps(d, separators=(",", ":")), cls=cls, kind=KIND[cls], sig_id=sig_id))

    def drop(self, t: datetime, cls: str, n: float = 1, key=None) -> None:
        if self.in_window(t):
            self.ctx.pop(cls, t, n, key=key)

    def gateway_calls(self) -> None:
        """Failed gateway calls, kept per cart at one constant rate (the same decision the gateway follows)."""
        n = self.ctx.keep_1_in(self.folder, "log.co_gw_timeout")
        T = self.tpl
        url = self.params["gateway_url"]
        first = self.sig.get("first_checkout_timeout", {"order": None, "sig_id": None})
        for q in self.m.requests():
            if not self.in_window(q.t):
                continue
            r = rng(self.ctx.seed, "cocall", q.order)
            th = f"http-nio-8080-exec-{r.randint(1, 200)}"
            if q.outcome == "gave_up":
                anchor = q.order == first["order"]
                t = q.t + timedelta(milliseconds=10023 if anchor else 10000 + r.randint(5, 40))
                if not q.kept(n):
                    self.drop(t, "log.co_gw_timeout", 2, key=q.cart)
                    continue
                self.add(q.pod, t, "ERROR", T["loggers"]["gw_client"], T["gw_timeout"].format(url=url, order=q.order), "log.co_gw_timeout",
                         th, first["sig_id"] if anchor else None, q.order, q.cart, stack=T["gw_timeout_stack"])
                t2 = t + timedelta(milliseconds=r.randint(3, 9))
                self.add(q.pod, t2, "WARN", T["loggers"]["request"], T["request_line"].format(method="POST", path="/api/checkout/submit",
                         status=504, ms=int((t2 - q.t).total_seconds() * 1000) + 6), "log.co_gw_timeout", th, None, q.order, q.cart)
            elif q.outcome == "pool_timeout":
                t = q.t + timedelta(milliseconds=q.gw_ms + r.randint(2, 6))
                if not q.kept(n):
                    self.drop(t, "log.co_gw_5xx", 2, key=q.cart)
                    continue
                self.add(q.pod, t, "ERROR", T["loggers"]["gw_client"], T["gw_5xx"].format(url=url, order=q.order, ms=q.gw_ms + 4),
                         "log.co_gw_5xx", th, None, q.order, q.cart)
                t2 = t + timedelta(milliseconds=r.randint(3, 9))
                self.add(q.pod, t2, "WARN", T["loggers"]["request"], T["request_line"].format(method="POST", path="/api/checkout/submit",
                         status=502, ms=int((t2 - q.t).total_seconds() * 1000) + 6), "log.co_gw_5xx", th, None, q.order, q.cart)
            elif q.outcome == "co_pool_timeout":
                self.db_timeout(q, th, r)

    def db_timeout(self, q, th: str, r) -> None:
        """A submit that timed out waiting for a checkout-db connection (checkout_db model): Hibernate's pair of
        lines, the exception handler's ERROR with the cause chain, and the 504 request line. No order exists yet."""
        n = self.ctx.keep_1_in(self.folder, "log.co_db_timeout")
        t = q.t + timedelta(milliseconds=q.co_ms)
        if not q.kept(n):
            self.drop(t, "log.co_db_timeout", 4, key=q.cart)
            return
        T, D = self.tpl, self.tpl["db_timeout"]
        co = self.m.co
        ca = self.ctx.scenario["estate"].get("co_anchor") or {}
        sig = ca.get("sig_id") if q.anchor and q.anchor == ca.get("id") else None
        kw = dict(timeout=co["connection_timeout_ms"], t=co["pool_per_pod"], a=co["pool_per_pod"], i=0, w=q.co_waiting, cart=q.cart)
        self.add(q.pod, t, "WARN", T["loggers"]["sql_helper"], D["warn"], "log.co_db_timeout", th, None, None, q.cart)
        self.add(q.pod, t, "ERROR", T["loggers"]["sql_helper"], D["error"].format(**kw), "log.co_db_timeout", th, sig, None, q.cart)
        t2 = t + timedelta(milliseconds=r.randint(1, 3))
        self.add(q.pod, t2, "ERROR", T["loggers"]["handler"], D["handler"].format(**kw), "log.co_db_timeout", th, None, None, q.cart,
                 stack=D["stack"].format(**kw))
        t3 = t2 + timedelta(milliseconds=r.randint(2, 6))
        self.add(q.pod, t3, "WARN", T["loggers"]["request"], T["request_line"].format(method="POST", path="/api/checkout/submit",
                 status=504, ms=int((t3 - q.t).total_seconds() * 1000) + 4), "log.co_db_timeout", th, None, None, q.cart)

    def audit(self) -> None:
        """The audit sink client's WARN for each audit write over its threshold (checkout_db model): every submit
        that got a connection on an affected pod writes `audit_writes_per_submit` events, each blocking for the
        sink's latency. One line in `keep_1_in` is kept, by count."""
        co = self.m.co
        if not co:
            return
        n = self.ctx.keep_1_in(self.folder, "log.co_audit_slow")
        T = self.tpl
        events = T["audit_events"]
        for q in self.m.requests():
            if q.outcome == "co_pool_timeout" or self.version_of(q.pod) not in self.m.co_versions or not self.in_window(q.t):
                continue
            per = q.co_ms / len(events)
            if per < co["audit_warn_over_ms"]:
                continue
            r = rng(self.ctx.seed, "audit", q.order)
            th = f"http-nio-8080-exec-{rng(self.ctx.seed, 'cocall', q.order).randint(1, 200)}"
            for k, ev in enumerate(events):
                t = q.borrow_t + timedelta(milliseconds=int(per * (k + 1)))
                ms = int(round(per * r.uniform(0.985, 1.015), -1))
                if every_nth(self.cnt, ("audit",), n):
                    self.add(q.pod, t, "WARN", T["loggers"]["audit"], T["audit_slow"].format(ms=ms, event=ev, depth=r.randint(480, 512)),
                             "log.co_audit_slow", th, cart=q.cart)
                else:
                    self.drop(t, "log.co_audit_slow")

    def requests(self) -> None:
        n = self.ctx.keep_1_in(self.folder, "log.co_request")
        T = self.tpl
        eps = list(self.m.traffic.keys())
        eps = [e for e in eps if e.startswith(("GET", "POST"))]
        r = rng(self.ctx.seed, "corq")
        acc = 0.0
        for w0, w1 in self.win:
            t = w0
            while t < w1:
                total = sum(self.m.traffic[e](t) for e in eps)
                self.drop(t, "log.co_request", total * 10)
                acc += total * 10
                while acc >= n:
                    acc -= n
                    x = r.random() * total
                    for e in eps:
                        x -= self.m.traffic[e](t)
                        if x <= 0:
                            break
                    method, path = e.split(" ")
                    pods = self.m.active_pods(t)
                    pod = pods[r.randrange(len(pods))][0]
                    at = t + timedelta(milliseconds=r.randint(0, 9999))
                    ms = r.randint(8, 60) if method == "GET" else r.randint(40, 180)
                    if path.endswith("submit"):
                        ms = r.randint(450, 900) + (2400 if r.random() < 0.25 else 0)
                        if self.m.co and self.version_of(pod) in self.m.co_versions:      # the audit writes hold the request
                            ms = max(ms, int(self.m.co_hold_s(at, self.version_of(pod)) * 1000))
                    self.add(pod, at, "INFO", T["loggers"]["request"], T["request_line"].format(method=method, path=path, status=200, ms=ms),
                             "log.co_request", f"http-nio-8080-exec-{r.randint(1, 200)}", cart=r.randint(*self.ctx.scenario["estate"]["cart_id_range"]))
                t += timedelta(seconds=10)

    def flood(self) -> None:
        """2.14.3 logs ~1,680 DEBUG lines a minute; one in `keep_1_in` is kept."""
        S = self.shape["debug_flood"]
        a, b = (self.c.t(x) for x in S["window"])
        per_min = S["lines_per_min"] / self.ctx.keep_1_in(self.folder, "log.co_debug_flood")
        r = rng(self.ctx.seed, "flood")
        T = self.tpl["debug_flood"]
        t = a
        while t < b:
            if any(v == "2.14.3" for _, v in self.m.active_pods(t)):
                self.drop(t, "log.co_debug_flood", S["lines_per_min"] / 6)
            t += timedelta(seconds=10)
        t = a
        while t < b:
            pods = [p for p, v in self.m.active_pods(t) if v == "2.14.3"]
            if pods:
                pod = pods[r.randrange(len(pods))]
                spec = T[r.randrange(len(T))]
                self.add(pod, t, "DEBUG", spec["logger"], spec["msg"].format(session=hexid(r, 12), cart=r.randint(*self.ctx.scenario["estate"]["cart_id_range"]),
                         variant=r.choice("AB")), "log.co_debug_flood", f"http-nio-8080-exec-{r.randint(1, 200)}")
                self.ctx.pop("log.co_debug_flood", t, -1)   # already counted in the true volume above
            t += timedelta(milliseconds=int(60000 / per_min * (0.5 + r.random())))

    def lifecycle(self) -> None:
        T = self.tpl
        P = self.pods
        r = rng(self.ctx.seed, "colife")
        for olds, news, readys, ver in ((P["initial"], P["rollout_2_14_3"], P["rollout_ready"], "2.14.3"),
                                        (P["rollout_2_14_3"], P["rollback_2_14_2"], P["rollback_ready"], "2.14.2")):
            for old, new, ready in zip(olds, news, readys):
                t_ready = self.c.t(ready)
                if new == P["readiness_failure"]["pod"]:
                    t_ready = self.c.t(P["readiness_failure"]["from"]) + timedelta(seconds=P["readiness_failure"]["seconds"])
                start = t_ready - timedelta(seconds=11.5)
                for k, (off, lvl, lg, msg) in enumerate(T["startup"]):
                    self.add(new, start + timedelta(seconds=off), lvl, T["loggers"][lg], msg.format(version=ver), "log.co_startup", "main", trace=False)
                for k, (off, lvl, lg, msg) in enumerate(T["shutdown"]):
                    self.add(old, t_ready + timedelta(seconds=off), lvl, T["loggers"][lg], msg, "log.co_shutdown", "SpringApplicationShutdownHook", trace=False)

    def background(self) -> None:
        r = rng(self.ctx.seed, "cobg")
        every = self.shape["checkout_background_every_s"]
        for w0, w1 in self.win:
            t = w0 + timedelta(seconds=r.randint(1, every))
            while t < w1:
                pods = self.m.active_pods(t)
                pod = pods[r.randrange(len(pods))][0]
                spec = self.tpl["background"][r.randrange(len(self.tpl["background"]))]
                self.add(pod, t, spec["level"], spec["logger"], spec["msg"].format(cart=r.randint(*self.ctx.scenario["estate"]["cart_id_range"]), n=r.randint(2, 9)),
                         "log.co_bg", f"http-nio-8080-exec-{r.randint(1, 200)}")
                t += timedelta(seconds=r.randint(every // 2, every * 3 // 2))

    def render(self) -> None:
        self.gateway_calls()
        self.audit()
        self.requests()
        self.flood()
        self.lifecycle()
        self.background()
        for pod, recs in sorted(self.recs.items()):
            recs = [x for x in recs if self.in_window(x.t)]
            if recs:
                self.ctx.write_log(f"splunk/raw/checkout_api/{pod}.jsonl", recs, "log_lines:splunk/raw/checkout_api")
