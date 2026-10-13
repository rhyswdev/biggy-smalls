"""Kubernetes events from k8s-prod-east as shipped to Splunk: splunk/raw/k8s_events/events.jsonl, one
core/v1 Event object per line."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from smalls.contracts.handles import k8s_handle
from smalls_data.gen.core import Ctx, Rec, hexid, iso_z, rng
from smalls_data.gen.model import Model


class K8s:
    def __init__(self, ctx: Ctx, model: Model):
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.pods = ctx.scenario["estate"]["checkout_pods"]
        self.ns = ctx.facts["parameters"]["checkout_api"]["namespace"]
        self.r = rng(ctx.seed, "k8s")
        self.recs: list[Rec] = []
        self.rv = 48812000
        self.items = {x["id"]: x for x in ctx.scenario["k8s"]}

    def ev(self, t: datetime, kind: str, name: str, reason: str, msg: str, component: str, typ: str = "Normal",
           ns: str | None = None, cls: str = "k8s.event", k: str = "noise", sig_id=None, last: datetime | None = None,
           count: int = 1, host: str | None = None, field_path: str | None = None) -> None:
        ns = self.ns if ns is None else ns
        self.rv += self.r.randint(3, 40)
        obj = {"apiVersion": "v1", "kind": "Event",
               "metadata": {"name": f"{name}.{hexid(self.r, 16)}", "namespace": ns or "default", "uid": self.uid(),
                            "resourceVersion": str(self.rv), "creationTimestamp": iso_z(t, 0)},
               "involvedObject": {"kind": kind, "namespace": ns, "name": name, "uid": self.uid(name),
                                  "apiVersion": "apps/v1" if kind in ("Deployment", "ReplicaSet") else ("batch/v1" if kind in ("Job", "CronJob") else
                                                ("autoscaling/v2" if kind == "HorizontalPodAutoscaler" else "v1"))},
               "reason": reason, "message": msg, "source": {"component": component},
               "firstTimestamp": iso_z(t, 0), "lastTimestamp": iso_z(last or t, 0), "count": count, "type": typ,
               "reportingComponent": component, "reportingInstance": host or ""}
        if not ns:
            obj["involvedObject"].pop("namespace")
        if field_path:
            obj["involvedObject"]["fieldPath"] = field_path
        if host:
            obj["source"]["host"] = host
        # The served object is the event as it stood at lastTimestamp (count included), so it is known from then.
        known = (last or t).replace(microsecond=0)
        self.recs.append(Rec(t=t, text=json.dumps(obj, separators=(",", ":")), cls=cls, kind=k, sig_id=sig_id, t_known=known))

    def uid(self, key: str | None = None) -> str:
        r = rng(self.ctx.seed, "uid", key) if key else self.r
        h = hexid(r, 32)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"

    def rollout(self, olds, news, readys, new_rs, old_rs, version, start, cls, kind, sig_id, failing=None):
        image = f"registry.larkspur.internal/commerce/checkout-api:{version}"
        self.ev(start, "Deployment", "checkout-api", "ScalingReplicaSet", f"Scaled up replica set {new_rs} to 1",
                "deployment-controller", cls=cls, k=kind, sig_id=sig_id)
        nodes = self.pods["nodes"]
        for i, (old, new, ready) in enumerate(zip(olds, news, readys)):
            t_ready = self.c.t(ready)
            created = t_ready - timedelta(seconds=11)
            node = nodes[(i + 2) % len(nodes)]
            self.ev(created, "ReplicaSet", new_rs, "SuccessfulCreate", f"Created pod: {new}", "replicaset-controller", cls=cls, k=kind)
            self.ev(created + timedelta(seconds=2), "Pod", new, "Started", "Started container checkout-api", "kubelet",
                    cls=cls, k=kind, host=node, field_path="spec.containers{checkout-api}")
            if failing and new == failing["pod"]:
                f0 = self.c.t(failing["from"])
                self.ev(f0, "Pod", new, "Unhealthy", "Readiness probe failed: HTTP probe failed with statuscode: 503", "kubelet",
                        typ="Warning", cls="k8s.readiness_fail", k="trap", sig_id=self.items["readiness_fail"]["sig_id"],
                        last=f0 + timedelta(seconds=failing["seconds"] - 5), count=failing["seconds"] // 5, host=node,
                        field_path="spec.containers{checkout-api}")
            self.ev(t_ready, "Deployment", "checkout-api", "ScalingReplicaSet", f"Scaled down replica set {old_rs} to {5 - i}",
                    "deployment-controller", cls=cls, k=kind)
            self.ev(t_ready + timedelta(seconds=1), "Pod", old, "Killing", "Stopping container checkout-api", "kubelet", cls=cls, k=kind,
                    host=nodes[i % len(nodes)], field_path="spec.containers{checkout-api}")

    def render(self) -> None:
        P = self.pods
        it = self.items
        self.rollout(P["initial"], P["rollout_2_14_3"], P["rollout_ready"], P["replicaset_2_14_3"], P["replicaset_2_14_2"], "2.14.3",
                     self.c.t(it["rollout_897"]["from"]), "k8s.rollout", "trap", it["rollout_897"]["sig_id"], P["readiness_failure"])
        self.rollout(P["rollout_2_14_3"], P["rollback_2_14_2"], P["rollback_ready"], P["replicaset_2_14_2"], P["replicaset_2_14_3"], "2.14.2",
                     self.c.t(it["rollback_898"]["from"]), "k8s.rollback", "signal", it["rollback_898"]["sig_id"])
        si = it["search_indexer_fail"]
        t = self.c.t(si["at"])
        self.ev(t - timedelta(seconds=184), "CronJob", "search-indexer", "SuccessfulCreate", "Created job search-indexer-29339160",
                "cronjob-controller", ns="search", cls="k8s.event")
        self.ev(t, "Job", "search-indexer-29339160", "BackoffLimitExceeded", "Job has reached the specified backoff limit", "job-controller",
                typ="Warning", ns="search", cls="k8s.event")
        sizes = [14, 16, 12]
        for t_s, size in zip(it["hpa_storefront"]["at"], sizes):
            why = "above target" if size > 12 else "All metrics below target"
            msg = (f"New size: {size}; reason: cpu resource utilization (percentage of request) {why}" if size > 12
                   else f"New size: {size}; reason: All metrics below target")
            self.ev(self.c.t(t_s), "HorizontalPodAutoscaler", "storefront-web", "SuccessfulRescale", msg, "horizontal-pod-autoscaler",
                    ns="storefront", cls="k8s.event")
        np_ = it["node_east_14_pressure"]
        self.ev(self.c.t(np_["at"]), "Node", "node-east-14", "NodeHasDiskPressure", "Node node-east-14 status is now: NodeHasDiskPressure",
                "kubelet", typ="Warning", ns="", cls="k8s.event", host="node-east-14")
        w0, w1 = (self.c.t(x) for x in self.ctx.scenario["log_windows"]["k8s_events"][0])
        recs = [r for r in self.recs if w0 <= r.t < w1]
        self.ctx.write_log("splunk/raw/k8s_events/events.jsonl", recs, "log_lines:splunk/raw/k8s_events", handle_fn=k8s_handle)
