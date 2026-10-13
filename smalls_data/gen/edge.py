"""The edge and the network: splunk/raw/f5/ltm.log (BIG-IP ltm via remote syslog, BSD syslog time with no
year), splunk/raw/nginx/edge-access.log (combined format plus upstream timing) and solarwinds/alerts.json
(a SWIS query result; times in the Orion server's zone with a seven-digit fraction and no marker)."""

from __future__ import annotations

from datetime import datetime, timedelta

from smalls.contracts.handles import solarwinds_handle
from smalls_data.gen.core import Ctx, Rec, content, local, rng, unit
from smalls_data.gen.model import Model
from smalls_data.lint_schema import _fields


def uptime(d: timedelta) -> str:
    s = int(d.total_seconds())
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}hr:{m}min{'s' if m != 1 else ''}:{s}sec"


class F5:
    """Member flaps of the payments gateway pool. A drained member finishes its in-flight transactions,
    its health check passes, it is re-enabled, and it fills again (PLAN 1.1, contributing factor 2)."""

    def __init__(self, ctx: Ctx, model: Model):
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.F = ctx.scenario["f5"]
        self.S = _fields("f5", "ltm")["strings"]
        self.net = ctx.facts["network"]

    def line(self, t: datetime, pid: int, msg: str) -> str:
        return f"{t.strftime('%b')} {t.day:2d} {t.strftime('%H:%M:%S')} lb-int-01 notice mcpd[{pid}]: {msg}"

    def render(self) -> None:
        recs = []
        pid = 7114
        # A member passes its health monitor once the application has started (about 13 s after the PSP
        # client line that marks the 3.8.0 start).
        last_up = {h: self.m.v380_at[h] + timedelta(seconds=14) for h in self.m.v380_at}
        for k, fl in enumerate(self.F["flaps"]):
            h = fl["member"]
            member = f"/Common/{self.net[h]}:{self.net['gateway_port']}"
            down, up = self.c.t(fl["down"]), self.c.t(fl["up"])
            err = f"{self.F['monitor']}: Response Code: 503 (Service Unavailable) @{down.strftime('%Y/%m/%d %H:%M:%S')}."
            text = self.S["member_down"].format(pool=self.F["pool"], member=member, monitor=self.F["monitor"], err=err, up=uptime(down - last_up[h]))
            recs.append(Rec(t=down, text=self.line(down, pid, text), cls=self.F["class"], kind=self.F["kind"], sig_id=fl.get("sig_id")))
            text = self.S["member_up"].format(pool=self.F["pool"], member=member, monitor=self.F["monitor"], down=uptime(up - down))
            recs.append(Rec(t=up, text=self.line(up, pid, text), cls=self.F["class"], kind=self.F["kind"]))
            last_up[h] = up
            self.ctx.pop(self.F["class"], down)
            self.ctx.pop(self.F["class"], up)
        self.ctx.write_log("splunk/raw/f5/ltm.log", recs, "log_lines:splunk/raw/f5")


class Nginx:
    """The edge in front of checkout-api: /api/checkout/* only. Submits come from the request stream and
    follow checkout-api's per-cart sampling decision, so a kept cart's 504s, 502s and resubmits appear here
    and in checkout-api's log with the same cartId."""

    def __init__(self, ctx: Ctx, model: Model):
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.N = content("nginx/noise.yaml")
        self.folder = "splunk/raw/nginx"
        self.win = [(self.c.t(a), self.c.t(b)) for a, b in ctx.scenario["log_windows"]["nginx"]]
        self.shape = ctx.scenario["log_shapes"]["nginx"]
        self.recs: list[Rec] = []
        self.pod_ip: dict[str, str] = {}

    def in_window(self, t: datetime) -> bool:
        return any(a <= t < b for a, b in self.win)

    def rate(self, cls: str) -> int:
        return self.ctx.keep_1_in(self.folder, cls)

    def client_ip(self, key) -> str:
        r = rng(self.ctx.seed, "client", key)
        return f"{r.choice([81, 86, 92, 109, 151, 176, 212])}.{r.randint(1, 254)}.{r.randint(0, 254)}.{r.randint(1, 254)}"

    def upstream(self, pod: str) -> str:
        if pod not in self.pod_ip:
            r = rng(self.ctx.seed, "podip", pod)
            self.pod_ip[pod] = f"{self.ctx.scenario['estate']['checkout_pods']['pod_ip_prefix']}{r.randint(0, 31)}.{r.randint(2, 254)}"
        return f"{self.pod_ip[pod]}:{self.shape['upstream_port']}"

    def line(self, t: datetime, ip: str, req: str, status: int, size: int, ref: str, ua: str, rt: float, upstream: str) -> str:
        ts = t.strftime("%d/%b/%Y:%H:%M:%S +0000")
        return (f'{ip} - - [{ts}] "{req}" {status} {size} "{ref}" "{ua}" rt={rt:.3f} uct="0.001" uht="{max(0.001, rt - 0.002):.3f}" '
                f'urt="{max(0.001, rt - 0.001):.3f}" upstream="{upstream}"')

    def add(self, t, text, cls, kind, kept=True, key=None):
        t = t.replace(microsecond=0)          # $time_local has one-second resolution
        if self.in_window(t):
            self.ctx.pop(cls, t, key=key)
            if kept:
                self.recs.append(Rec(t=t, text=text, cls=cls, kind=kind))

    def submits(self) -> None:
        n_err, n_ok = self.rate("nginx.submit_5xx"), self.rate("nginx.submit")
        uas = self.N["user_agents"]
        for q in self.m.requests():
            if not self.in_window(q.t):
                continue
            r = rng(self.ctx.seed, "edge", q.order)
            ua = uas[int(unit(self.ctx.seed, "ua", q.cart) * len(uas))]
            ip = self.client_ip(q.cart)
            req = f"POST /api/checkout/submit?cartId={q.cart} HTTP/1.1"
            if q.outcome in ("gave_up", "pool_timeout"):
                rt = 10.0 + r.randint(9, 49) / 1000 if q.outcome == "gave_up" else (q.gw_ms + r.randint(10, 30)) / 1000
                status, size = (504, 167) if q.outcome == "gave_up" else (502, 158)
                cls, kind, keep = "nginx.submit_5xx", "signal", q.kept(n_err)
            else:
                rt = (q.gw_ms + r.randint(40, 160)) / 1000
                status, size = 200, r.randint(310, 420)
                cls = "nginx.cart_resubmit" if q.resubmit else "nginx.submit"
                kind, keep = "noise", q.kept(n_ok)
            t = q.t + timedelta(seconds=rt)
            self.add(t, self.line(t, ip, req, status, size, self.N["referers"]["submit"], ua, rt, self.upstream(q.pod)) if keep else "",
                     cls, kind, keep, key=q.cart)

    def other_checkout(self) -> None:
        n = self.rate("nginx.request")
        r = rng(self.ctx.seed, "edge-other")
        eps = {"GET /api/checkout/summary": "summary", "POST /api/checkout/address": "address"}
        acc = 0.0
        for w0, w1 in self.win:
            t = w0
            while t < w1:
                total = sum(self.m.traffic[e](t) for e in eps)
                acc += total * 10
                self.ctx.pop("nginx.request", t, total * 10)
                while acc >= n:
                    acc -= n
                    e = r.choice(list(eps))
                    at = t + timedelta(seconds=r.randint(0, 9))
                    method, path = e.split(" ")
                    pods = self.m.active_pods(at)
                    pod = pods[r.randrange(len(pods))][0]
                    rt = r.randint(9, 70) / 1000 if method == "GET" else r.randint(40, 190) / 1000
                    cart = r.randint(*self.ctx.scenario["estate"]["cart_id_range"])
                    self.recs.append(Rec(t=at, text=self.line(at, self.client_ip(cart), f"{method} {path}?cartId={cart} HTTP/1.1", 200,
                                                              r.randint(180, 2400), self.N["referers"][eps[e]],
                                                              self.N["user_agents"][r.randrange(len(self.N["user_agents"]))], rt, self.upstream(pod)),
                                         cls="nginx.request", kind="noise"))
                t += timedelta(seconds=10)

    def scanner(self) -> None:
        S = self.N["scanner"]
        r = rng(self.ctx.seed, "scanner")
        for b in S["bursts"]:
            t = self.c.t(b)
            for p in S["paths"]:
                t += timedelta(milliseconds=r.randint(80, 900))
                self.add(t, self.line(t, S["ip"], f"GET {p} HTTP/1.1", 404, 153, "-", S["user_agent"], r.randint(1, 4) / 1000, "-"),
                         "nginx.scanner", "noise")

    def render(self) -> None:
        self.submits()
        self.other_checkout()
        self.scanner()
        self.ctx.write_log("splunk/raw/nginx/edge-access.log", [x for x in self.recs if self.in_window(x.t)], "log_lines:splunk/raw/nginx")


class SolarWinds:
    """Orion alerts as a SWIS query result. pay-03's HTTP component goes down when its health endpoint
    fails, about four minutes after the F5 first drains it; nothing on the node itself is wrong."""

    def __init__(self, ctx: Ctx):
        self.ctx, self.c = ctx, ctx.clock
        self.zone = ctx.facts["zones"]["solarwinds"]["zone"]

    def ts(self, t: datetime) -> str:
        lt = local(t, self.zone)
        return lt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{lt.microsecond:06d}0"

    def render(self) -> None:
        out = []
        for k, a in enumerate(self.ctx.scenario["solarwinds"]):
            trig = self.c.t(a["triggered"][:12])
            trig = trig.replace(microsecond=int(a["triggered"].split(".")[1][:6]))
            reset = None
            if a.get("reset"):
                reset = self.c.t(a["reset"][:12]).replace(microsecond=int(a["reset"].split(".")[1][:6]))
            node = f"{a['entity'].upper()}.LARKSPUR.INTERNAL"
            comp = a["component"]
            if a["name"] == "Application Component down":
                obj = 4500 + sum(map(ord, a["entity"] + comp)) % 97      # one Orion object per component
                caption, etype, url = comp, "Orion.APM.Component", f"/Orion/APM/MonitorDetails.aspx?NetObject=AM:{obj}"
                msg = f"Component {comp} on {node} is Down"
            else:
                caption, etype, url = comp, "Orion.Volumes", f"/Orion/NetPerfMon/VolumeDetails.aspx?NetObject=V:{900 + a['id'] % 89}"
                msg = f"Volume {comp} on {node} is 92 % full"
            out.append({"AlertActiveID": a["id"], "AlertObjectID": 3000 + sum(map(ord, a["entity"] + comp)) % 1000, "AlertName": a["name"], "EntityCaption": caption,
                        "EntityType": etype, "EntityDetailsUrl": url, "RelatedNodeCaption": node, "Severity": 2 if "Component" in a["name"] else 1,
                        "TriggeredDateTime": self.ts(trig), "TriggeredMessage": msg, "ResetDateTime": self.ts(reset) if reset else None,
                        "Acknowledged": False, "AcknowledgedBy": None, "AcknowledgedDateTime": None, "Notes": ""})
            self.ctx.row(solarwinds_handle(a["id"]), "solarwinds/alerts.json", f"/results/{len(out) - 1}", trig, "sw.alert",
                         a["kind"], a.get("sig_id"))
        self.ctx.write_json("solarwinds/alerts.json", {"results": out})
        self.ctx.count("records:solarwinds_alerts", len(out))
