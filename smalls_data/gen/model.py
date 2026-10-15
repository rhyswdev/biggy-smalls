"""The world model behind every log and metric: the connection budget, the pool state per VM, and one
simulated stream of checkout submits, each calling the gateway's authorize endpoint. Logs and metrics
sample from the same stream, so the numbers they show agree."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from smalls_data.gen.core import Ctx, Curve, floor_minute, rng, unit

HOSTS = ["pay-01", "pay-02", "pay-03", "pay-04"]


@dataclass
class Req:
    t: datetime              # checkout-api sends the authorize call (gateway receives it ~2 ms later)
    host: str                # gateway VM
    order: str
    cart: int
    pod: str
    eu: bool
    slow: bool               # PayCore attempts exceed the client timeout
    version: str             # gateway version serving it
    outcome: str             # ok | slow_ok | gave_up | pool_timeout | fail_fast
    psp_ms: int              # latency of the (last) PSP attempt
    gw_ms: int               # gateway time to respond
    borrow_t: datetime       # connection borrowed (transaction start)
    attempts: int
    thread: int
    resubmit: bool = False
    u: float = 1.0           # per-cart sampling value: a log keeps the request when u < 1 / keep_1_in
    anchor: str | None = None   # gateway_signals id of a request every log keeps (at most one per host)
    co_ms: int = 0           # time checkout-api holds its checkout-db connection beyond the gateway call (checkout_db model)
    co_waiting: int = 0      # threads waiting on the pod's checkout-db pool when this request timed out there

    def kept(self, n: int) -> bool:
        return self.anchor is not None or self.u < 1 / n


class Model:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        sc = ctx.scenario
        m = sc["model"]
        c = ctx.clock
        self.c = c
        self.rate = Curve(m["authorization_rate_per_s"], c)
        self.slow_share = Curve(m["slow_share"], c)
        self.eu_share = m["eu_share"]
        self.err_rate = Curve(m["checkout_error_rate_pct"], c) if "checkout_error_rate_pct" in m else None
        self.p90 = Curve(m["gw_latency_p90_s"], c)
        self.p99 = Curve(m["gw_latency_p99_s"], c)
        self.success = Curve(m["payment_success_rate_pct"], c)
        self.traffic = {k: Curve(v, c) for k, v in m["traffic_per_s"].items()}
        self.waiting = Curve(m["connections"]["hikari_waiting"], c)
        self.conn_rows = [(c.t(r["at"]), r) for r in m["connections"]["rows"]]
        self.lat = m["psp_latency_ms"]
        self.estate = sc["estate"]
        gw = ctx.facts["parameters"]["payments_gateway"]
        self.versions = gw["versions"]
        self.max_pool = gw["hikari"]["maximumPoolSize"]
        # Gateway version per host over time. A scenario with no 3.7.9 restart keeps 3.8.0 past the bundle's end.
        sig = {s["id"]: s for s in sc["gateway_signals"]}
        never = c.t("23:59:59")
        self.v380_at = {h: c.t(t) for h, t in sig["startup_380"]["hosts"].items()}
        self.v379_at = {h: c.t(t) for h, t in sig["startup_379"]["hosts"].items()} if "startup_379" in sig else {h: never for h in self.v380_at}
        self.recovered = c.t("15:24:00")
        # The anchored requests (the first retry, the first gateway pool timeout) exist only where the scenario has them.
        self.anchor = sig.get("first_retry")
        self.first_slow = c.t(self.anchor["borrow_at"]) if self.anchor else never
        self.first_pt = sig.get("first_pool_timeout")
        self.first_pt_t = c.t(self.first_pt["at"]) if self.first_pt else never
        self._reqs: list[Req] | None = None
        self._submits: dict[int, int] = {}
        self.per_cart_max = m["resubmit"]["per_cart"][1]
        # checkout-api's own pool to checkout-db (a scenario without `checkout_db` never touches it): on the pods of
        # the versions listed, every submit holds its connection for `base_hold_s` plus one blocking audit write per
        # event at the sink's latency; by Little's law the pool serves pool / hold submits a second and the rest time
        # out waiting for a connection.
        self.co = m.get("checkout_db")
        if self.co:
            self.sink_s = Curve(self.co["audit_sink_latency_s"], c)
            self.co_versions = set(self.co["versions"])
            self.err_rate = self.derived_error_rate

    # -- connection budget -------------------------------------------------------------------

    def conn_row(self, t: datetime) -> dict:
        row = self.conn_rows[0][1]
        for at, r in self.conn_rows:
            if at <= t:
                row = r
        return row

    def host_conns(self, t: datetime, host: str) -> int:
        return self.conn_row(t)["gateway"][HOSTS.index(host)]

    def saturated(self, t: datetime, host: str) -> bool:
        """A pool that cannot grow: at its maximum, or refused by the database (pay-04 from 14:33)."""
        row = self.conn_row(t)
        total = sum(row["gateway"]) + row["order"] + row["recon"] + row["other"]
        n = self.host_conns(t, host)
        if t >= self.v379_at[host] and t < self.v379_at[host] + timedelta(minutes=3):
            return False
        return n >= self.max_pool or (total >= self.ctx.scenario["model"]["connections"]["usable"] and t < self.c.t("15:06"))

    def version(self, t: datetime, host: str) -> str:
        if t >= self.v379_at[host]:
            return "3.7.9"
        if t >= self.v380_at[host]:
            return "3.8.0"
        return "3.7.9"

    def hold_s(self, t: datetime) -> float:
        return 1.05 + 36 * self.slow_share(t)

    def unserved_fraction(self, t: datetime, host: str) -> float:
        if not self.saturated(t, host) or self.version(t, host) != "3.8.0":
            return 0.0
        # The anchored first pool timeout on its host (PLAN 1.4, 14:31:07) is the first one there.
        if host == self.first_pt["host"] and t < self.first_pt_t - timedelta(milliseconds=5030):
            return 0.0
        demand = self.rate(t) / 4 * self.hold_s(t)
        cap = self.host_conns(t, host)
        return min(0.6, max(0.15, 1 - cap / demand))

    # -- checkout-api's pool to checkout-db (scenarios with `model.checkout_db`) ------------------

    def co_hold_s(self, t: datetime, version: str) -> float:
        """Seconds a submit holds its checkout-db connection on a pod of `version`, gateway call included."""
        base = self.co["base_hold_s"]
        if version not in self.co_versions:
            return base
        return base + self.co["audit_writes_per_submit"] * self.sink_s(t)

    def co_demand(self, t: datetime, version: str) -> float:
        """Connections a pod's submits would hold at t (Little's law: arrivals a second times hold)."""
        return self.traffic["POST /api/checkout/submit"](t) / len(self.estate["checkout_pods"]["initial"]) * self.co_hold_s(t, version)

    def co_fail(self, t: datetime, version: str) -> float:
        """Share of a pod's submits that time out waiting for a checkout-db connection."""
        return max(0.0, 1 - self.co["pool_per_pod"] / self.co_demand(t, version))

    def co_active(self, t: datetime, version: str) -> float:
        """Active connections in a pod's pool: submit demand capped at the pool, plus the other endpoints' brief use."""
        return min(float(self.co["pool_per_pod"]), self.co_demand(t, version) + self.co["other_active"])

    def co_waiting(self, t: datetime, version: str) -> int:
        """Threads queued on a saturated pool: arrivals a second times their wait (a timeout waits the full
        connectionTimeout, a submit that gets a connection waits about half of it)."""
        f = self.co_fail(t, version)
        lam = self.traffic["POST /api/checkout/submit"](t) / len(self.estate["checkout_pods"]["initial"])
        wait = self.co["connection_timeout_ms"] / 1000
        return int(round(lam * (f * wait + (1 - f) * wait / 2))) if f > 0 else 0

    def version_share(self, t: datetime) -> float:
        pods = self.active_pods(t)
        return sum(1 for _, v in pods if v in self.co_versions) / len(pods)

    def derived_error_rate(self, t: datetime) -> float:
        """checkout-api 5xx as a percent of all its requests: the baseline plus the submits that time out on
        checkout-db, on the share of pods running an affected version (the monitor and the metric read this)."""
        total = sum(self.traffic[e](t) for e in self.traffic if e.startswith(("GET", "POST")))
        submits = self.traffic["POST /api/checkout/submit"](t)
        share = self.version_share(t + timedelta(seconds=30))
        return self.co["baseline_error_pct"] + 100 * submits * share * self.co_fail(t, next(iter(self.co_versions))) / total

    # -- pods ----------------------------------------------------------------------------------

    def active_pods(self, t: datetime) -> list[tuple[str, str]]:
        """(pod, version) serving at t."""
        p = self.estate["checkout_pods"]
        if not hasattr(self, "_ready"):
            self._ready = ([self.c.t(x) for x in p["rollout_ready"]], [self.c.t(x) for x in p["rollback_ready"]])
        up, back = self._ready
        out = []
        for i in range(6):
            if t < up[i]:
                out.append((p["initial"][i], "2.14.2"))
            elif t < back[i]:
                out.append((p["rollout_2_14_3"][i], "2.14.3"))
            else:
                out.append((p["rollback_2_14_2"][i], "2.14.2"))
        return out

    # -- the request stream ------------------------------------------------------------------

    def windows(self) -> list[tuple[datetime, datetime]]:
        return [(self.c.t(a), self.c.t(b)) for a, b in self.ctx.scenario["log_windows"]["payments_app"]]

    def requests(self) -> list[Req]:
        if self._reqs is None:
            self._reqs = self._simulate()
        return self._reqs

    def _latency(self, r, t: datetime, eu: bool) -> int:
        if not eu:
            return r.randint(*self.lat["non_eu"])
        for band in self.lat["eu_ramp"]:
            if self.c.t(band["from"]) <= t < self.c.t(band["to"]):
                return r.randint(*band["range"])
        return r.randint(*self.lat["eu_baseline"])

    def _simulate(self) -> list[Req]:
        ctx = self.ctx
        r = rng(ctx.seed, "requests")
        out: list[Req] = []
        cart_lo, cart_hi = self.estate["cart_id_range"]
        resub_from = self.c.t(self.ctx.scenario["model"]["resubmit"]["from"])
        resub_to = self.c.t(self.ctx.scenario["model"]["resubmit"]["until"])
        recent_failed: list[int] = []
        acc = 0.0
        for w0, w1 in self.windows():
            t = w0
            while t < w1:
                acc += self.rate(t)
                n = int(acc)
                acc -= n
                for k in range(n):
                    rt = t + timedelta(milliseconds=int((k + r.random()) * 1000 / max(n, 1)))
                    out.append(self._one(r, rt, recent_failed, cart_lo, cart_hi, resub_from, resub_to))
                t += timedelta(seconds=1)
        out.sort(key=lambda q: q.t)
        if not self.anchor:
            return self._number(out)
        anchor_t = self.c.t(self.anchor["borrow_at"])
        # The anchored first retried order (PLAN 1.4 14:26:04 on pay-01) replaces the nearest request.
        i = min(range(len(out)), key=lambda j: abs((out[j].t - anchor_t).total_seconds()) + (0 if out[j].host == "pay-01" else 99))
        q = out[i]
        q.t = anchor_t - timedelta(milliseconds=2)
        q.host, q.eu, q.slow, q.version = "pay-01", True, True, "3.8.0"
        q.thread = int(self.anchor["thread"].rsplit("-", 1)[1])
        q.pod = "checkout-api-6d8c9b7f4d-q8m4z"
        q.anchor = self.anchor["id"]
        self._finish(r, q)
        q.borrow_t = anchor_t
        # The anchored first pool timeout: the earliest-ending one on its host ends exactly at its time.
        h = self.first_pt["host"]
        p = min((x for x in out if x.host == h and x.outcome == "pool_timeout"), key=lambda x: x.t + timedelta(milliseconds=x.gw_ms))
        p.t = self.first_pt_t - timedelta(milliseconds=p.gw_ms)
        p.borrow_t = p.t
        p.thread = int(self.first_pt["thread"].rsplit("-", 1)[1])
        p.anchor = self.first_pt["id"]
        out.sort(key=lambda x: x.t)
        # Order ids are sequential in arrival order; the anchored request carries its fixed id.
        i = next(j for j, x in enumerate(out) if x is q)
        base = int(self.anchor["order"].split("-")[1]) - i
        for j, req in enumerate(out):
            req.order = f"ord-{base + j}"
        return out

    def _number(self, out: list[Req]) -> list[Req]:
        """A stream with no anchored gateway requests: the first checkout-db timeout is the anchor every log keeps
        (estate.co_anchor), and order ids run from the request nearest estate.order_anchor."""
        ca = self.estate.get("co_anchor")
        if ca:
            q = next(x for x in out if x.outcome == "co_pool_timeout")
            q.anchor = ca["id"]
        oa = self.estate["order_anchor"]
        at = self.c.t(oa["at"])
        i = min(range(len(out)), key=lambda j: abs((out[j].t - at).total_seconds()))
        base = int(oa["order"].split("-")[1]) - i
        for j, req in enumerate(out):
            req.order = f"ord-{base + j}"
        return out

    def _one(self, r, t, recent_failed, cart_lo, cart_hi, resub_from, resub_to) -> Req:
        host = HOSTS[r.randrange(4)]
        eu = r.random() < self.eu_share
        v = self.version(t, host)
        # ord-7781901 (borrowed 14:26:04.031 on pay-01) is the first slow request anywhere (PLAN 1.4).
        slow = eu and self.first_slow <= t < self.recovered and r.random() < self.slow_share(t) / self.eu_share
        pods = self.active_pods(t)
        pod = pods[r.randrange(len(pods))][0]
        resubmit = False
        if resub_from <= t < resub_to and recent_failed and r.random() < 0.4:
            cart = recent_failed.pop(r.randrange(len(recent_failed)))
            resubmit = True
        else:
            cart = r.randint(cart_lo, cart_hi)
        q = Req(t=t, host=host, order="", cart=cart, pod=pod, eu=eu, slow=slow, version=v, outcome="ok",
                psp_ms=0, gw_ms=0, borrow_t=t, attempts=1, thread=r.randint(1, 200), resubmit=resubmit,
                u=unit(self.ctx.seed, "cart", cart))
        if r.random() < self.unserved_fraction(t, host):
            q.outcome, q.gw_ms, q.attempts = "pool_timeout", 5000 + r.randint(1, 30), 0
        else:
            self._finish(r, q)
        if self.co:
            self._checkout_db(q, dict(pods)[pod])
        self._submits[cart] = self._submits.get(cart, 0) + 1
        # A failed cart may be resubmitted until it has been submitted per_cart[1] times (PLAN 1.1: 2 to 5).
        if q.outcome in ("gave_up", "pool_timeout", "co_pool_timeout") and self._submits[cart] < self.per_cart_max:
            recent_failed.append(cart)
            if len(recent_failed) > 400:
                del recent_failed[:200]
        return q

    def _checkout_db(self, q: Req, pod_version: str) -> None:
        """On an affected pod a submit either times out waiting for a checkout-db connection (it never reaches the
        gateway) or gets one and holds it through its blocking audit writes. Its own random stream, so a scenario
        without the model draws exactly what it always drew."""
        if pod_version not in self.co_versions:
            return
        r = rng(self.ctx.seed, "co", q.t.isoformat(), q.cart)
        if r.random() < self.co_fail(q.t, pod_version):
            q.outcome, q.gw_ms, q.psp_ms, q.attempts = "co_pool_timeout", 0, 0, 0
            q.co_ms = self.co["connection_timeout_ms"] + r.randint(1, 30)
            q.co_waiting = max(1, self.co_waiting(q.t, pod_version) + r.randint(-2, 2))
        else:
            q.co_ms = int(self.co["audit_writes_per_submit"] * self.sink_s(q.t) * 1000 * r.uniform(0.97, 1.03))

    def _finish(self, r, q: Req) -> None:
        q.borrow_t = q.t + timedelta(milliseconds=r.randint(2, 9))
        cfg = self.versions[q.version]
        if q.slow:
            if cfg["psp_retries"] == 0:
                q.outcome, q.attempts, q.psp_ms = "fail_fast", 1, cfg["psp_timeout_ms"]
                q.gw_ms = cfg["psp_timeout_ms"] + r.randint(20, 60)
            else:
                q.outcome, q.attempts = "gave_up", cfg["psp_retries"] + 1
                q.psp_ms = cfg["psp_timeout_ms"]
                q.gw_ms = cfg["psp_timeout_ms"] * q.attempts + sum(cfg["backoff_ms"]) + r.randint(10, 40)
        else:
            q.psp_ms = self._latency(r, q.t, q.eu)
            q.gw_ms = q.psp_ms + r.randint(15, 60)
            q.outcome = "slow_ok" if q.psp_ms >= 5000 else "ok"

    # -- pool stats ----------------------------------------------------------------------------

    def stats(self, t: datetime, host: str) -> tuple[int, int, int, int]:
        """(total, active, idle, waiting) for a housekeeper line."""
        total = self.host_conns(t, host)
        if self.saturated(t, host) and self.version(t, host) == "3.8.0":
            w = int(round(self.waiting(t)))
            r = rng(self.ctx.seed, "stats", host, t.isoformat())
            w = max(0, w + r.randint(-2, 2))
            return total, total, 0, w
        r = rng(self.ctx.seed, "stats", host, t.isoformat())
        idle = min(total, max(1, int(round(total * (0.75 if self.slow_share(t) == 0 else 0.2))) + r.randint(-1, 1)))
        return total, total - idle, idle, 0


def minutes(a: datetime, b: datetime):
    t = floor_minute(a)
    while t < b:
        yield t
        t += timedelta(minutes=1)
