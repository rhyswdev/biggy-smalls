import { describe, expect, it } from "vitest";
import { plainLabel, windowText } from "@/lib/chat/labels";

// v4 plan 5.1: one case per tool, and the fallback. Generic arguments, no incident detail.
describe("plainLabel", () => {
  it.each([
    ["search_logs", { terms: ["timeout", "retry"], sourcetype: "app:web", since: "-2h" }, "Search app:web logs for “timeout” and “retry” over the last 2 hours"],
    ["search_logs", { since: "-30m", group_by: "level" }, "Search every log over the last 30 minutes, counted by level"],
    ["list_alerts", { since: "-2h" }, "List active alerts over the last 2 hours"],
    ["list_alerts", { state: "all", text: "disk" }, "List alerts, cleared ones too, about “disk”"],
    ["get_record", { handle: "x:1" }, "Open the record x:1"],
    ["query_metrics", { metric: "cpu.load", scope: "host:a", since: "14:00", until: "14:30" }, "Chart cpu.load for host:a from 14:00 to 14:30"],
    ["list_changes", { since: "-24h" }, "List changes over the last 24 hours"],
    ["itsm_query", { table: "journal", text: "restart" }, "Look up work notes in the service desk for “restart”"],
    ["search_knowledge", { query: "queue backlog" }, "Search the knowledge base for “queue backlog”"],
    ["get_topology", { entity: "svc-a", hops: 2 }, "Map what svc-a connects to, two steps out"],
    ["get_oncall", { service: "Billing" }, "Find who is on call for Billing"],
    ["read_chat", { channel: "ops", text: "deploy", since: "-1h" }, "Look in #ops for “deploy” over the last hour"],
    ["check_status_page", { provider: "Acme" }, "Check Acme’s status page"],
    ["propose_action", { kind: "rollback", target: "svc-a" }, "Propose: rollback on svc-a"],
  ])("%s %j", (tool, args, now) => {
    const label = plainLabel({ tool, args });
    expect(label.now).toBe(now);
    expect(label.past.charAt(0)).toBe(label.past.charAt(0).toLowerCase());
  });

  it("falls back to the model's reason, then to the tool's name", () => {
    expect(plainLabel({ tool: "new_tool", args: {}, rationale: { tests: "whether the queue drained" } }))
      .toEqual({ now: "Whether the queue drained", past: "checked whether the queue drained" });
    expect(plainLabel({ tool: "new_tool", args: {} }).now).toBe("New tool");
    expect(plainLabel({ tool: "get_record", args: {}, rationale: { tests: "the runbook" } }).now).toBe("The runbook");
  });

  it("writes a window from relative and clock times", () => {
    expect(windowText({})).toBe("");
    expect(windowText({ since: "-1d" })).toBe(" over the last day");
    expect(windowText({ since: "2026-01-01T09:05:00Z" })).toBe(" since 09:05");
    expect(windowText({ until: "10:00" })).toBe(" up to 10:00");
  });
});
