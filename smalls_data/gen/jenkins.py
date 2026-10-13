"""Jenkins: the JSON API (api/json?depth=1) of a FreeStyleBuild, served as version snapshots the way the
notification integration received them (ruling D1b): jenkins/jobs/<job>/builds/<n>.building.json when the
build started (building, result null, duration 0) and <n>.json once it finished."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from smalls.contracts.handles import jenkins_build_handle, jenkins_commit_handle
from smalls_data.gen.core import Ctx, content, local, ms, rng

BASE = "https://jenkins.larkspur.internal"


class Jenkins:
    def __init__(self, ctx: Ctx):
        self.ctx, self.c = ctx, ctx.clock

    def change_items(self, ref: str, job: str) -> list[dict]:
        items = []
        for it in content(ref)["items"]:
            p = self.ctx.person(it["author"])
            t = self.c.t(it["at"])
            sha = hashlib.sha1(f"{job}|{it['msg']}|{it['at']}".encode()).hexdigest()
            date = local(t, p.get("tz", "UTC")).strftime("%Y-%m-%d %H:%M:%S %z")
            items.append({"_class": "hudson.plugins.git.GitChangeSet", "affectedPaths": it["paths"], "commitId": sha,
                          "timestamp": ms(t), "author": {"absoluteUrl": f"{BASE}/user/{it['author']}", "fullName": p["name"]},
                          "authorEmail": f"{it['author']}@larkspur.com", "comment": it["msg"] + "\n", "date": date, "id": sha,
                          "msg": it["msg"], "paths": [{"editType": "edit", "file": f} for f in it["paths"]]})
        return items

    def render(self) -> None:
        ctx = self.ctx
        export = self.c.t("15:30")
        for b in ctx.scenario["jenkins"]:
            job, n = b["job"], b["number"]
            t0 = self.c.t(b["start"])
            if t0 >= export:
                continue
            dur_ms = b["duration"] * 1000 + rng(ctx.seed, "jkdur", job, n).randint(0, 999)
            end = t0 + timedelta(milliseconds=dur_ms)
            if b.get("user"):
                p = ctx.person(b["user"])
                cause = {"_class": "hudson.model.Cause$UserIdCause", "shortDescription": f"Started by user {p['name']}",
                         "userId": b["user"], "userName": p["name"]}
            else:
                cause = {"_class": "hudson.triggers.TimerTrigger$TimerTriggerCause", "shortDescription": "Started by timer"}
            params = [{"_class": "hudson.model.StringParameterValue", "name": k, "value": str(v)} for k, v in b["params"].items()]
            actions = [{"_class": "hudson.model.CauseAction", "causes": [cause]}]
            if params:
                actions.insert(0, {"_class": "hudson.model.ParametersAction", "parameters": params})
            items = self.change_items(b["changeset"], job) if b.get("changeset") else []
            agent = "jenkins-agent-03" if job == "payments-reconciler" else rng(ctx.seed, "agent", job, n).choice(["jenkins-agent-01", "jenkins-agent-02"])

            # Jenkins estimates from the job's recent successful builds, never from this build's own end.
            prior = [x for x in ctx.scenario["jenkins"] if x["job"] == job and x["number"] < n and x["result"] == "SUCCESS"][-3:]
            estimate = (sum(x["duration"] for x in prior) // len(prior) if prior else b["duration"]) * 1000

            def snapshot(done: bool) -> dict:
                return {"_class": "hudson.model.FreeStyleBuild", "actions": actions, "artifacts": [], "building": not done,
                        "description": None, "displayName": f"#{n}", "duration": dur_ms if done else 0,
                        "estimatedDuration": estimate, "executor": None if done else {"_class": "hudson.model.Executor"},
                        "fullDisplayName": f"{job} #{n}", "id": str(n), "keepLog": False, "number": n,
                        "queueId": 90000 + n, "result": b["result"] if done else None, "timestamp": ms(t0),
                        "url": f"{BASE}/job/{job}/{n}/", "builtOn": agent,
                        "changeSet": ({"_class": "hudson.plugins.git.GitChangeSetList", "items": items, "kind": "git"} if items
                                      else {"_class": "hudson.scm.EmptyChangeLogSet", "items": [], "kind": None})}

            building = f"jenkins/jobs/{job}/builds/{n}.building.json"
            ctx.write_json(building, snapshot(False))
            ctx.row(jenkins_build_handle(job, n), building, "/", t0, "jk.build", b["kind"], b.get("sig_id"), valid_from=t0,
                    valid_to=end if end <= export else None)
            src = content(b["changeset"])["items"] if b.get("changeset") else []
            for i, (it, raw) in enumerate(zip(items, src)):
                ctx.row(jenkins_commit_handle(job, n, it["commitId"]), building, f"/changeSet/items/{i}", t0, "jk.commit", b["kind"],
                        raw.get("sig_id"), valid_from=t0, valid_to=end if end <= export else None)
            if end <= export:
                final = f"jenkins/jobs/{job}/builds/{n}.json"
                ctx.write_json(final, snapshot(True))
                ctx.row(jenkins_build_handle(job, n), final, "/", t0, "jk.build", b["kind"], b.get("sig_id"), t_known=end, valid_from=end)
                for i, (it, raw) in enumerate(zip(items, src)):
                    ctx.row(jenkins_commit_handle(job, n, it["commitId"]), final, f"/changeSet/items/{i}", t0, "jk.commit", b["kind"],
                            raw.get("sig_id"), t_known=end, valid_from=end)
            ctx.count("records:jenkins_builds")
