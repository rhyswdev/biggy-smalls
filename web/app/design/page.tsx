import { Button, Confidence, ConflictPair, EvidenceRecord, Handles, Label, Notice, StakeholderMessage, StatusTag, Toggle, ToolResult } from "@/components/ds";
import { STATUS } from "@/components/ds/StatusTag";

export const metadata = { title: "Smalls design system" };

// Generic sample values: nothing here is from any incident's data.
const HANDLES = ["log:web-01/1a2b3c4d", "q:5e6f7a8b", "slack:ops/9c0d1e2f"];
const RESULT = {
  id: "call_07", status: "ok", text: "3 log records, 2026-01-01T09:00:00Z to 2026-01-01T09:30:00Z\nlog:web-01/1a2b3c4d  09:12:40  web-01  ERROR [worker] job failed: timeout\n[3 records]",
  window: { since: "2026-01-01T09:00:00Z", until: "2026-01-01T09:30:00Z", clamped: true }, sampling: { keep_1_in: 10, per: "host" },
  handles: HANDLES.slice(0, 1), native_query: 'search index=app "timeout" earliest="2026-01-01T09:00:00Z" latest="2026-01-01T09:30:00Z"',
};

function Pane({ theme }: { theme: "light" | "dark" }) {
  return (
    <section data-theme={theme} aria-label={`Components, ${theme} theme`} data-pane={theme}
      style={{ flex: "1 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 20, padding: 24, background: "var(--surface)", color: "var(--ink)" }}>
      <h2 className="heading" style={{ margin: 0 }}>{theme === "light" ? "Light" : "Dark"}</h2>
      <div data-component="Button" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button variant="primary">Approve</Button><Button>Edit</Button><Button variant="quiet">Skip</Button><Button variant="danger">Reject…</Button>
        <Button size="sm">Small</Button><Button variant="primary" disabled>Disabled</Button>
      </div>
      <div data-component="Toggle"><Toggle checked label="Auto-approve reads" /> <Toggle checked={false} label="Auto-approve reads" hint="Smalls asks before every read" /></div>
      <div data-component="Handle"><Handles ids={HANDLES} active={HANDLES[1]} /></div>
      <div data-component="StatusTag" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{Object.keys(STATUS).map((s) => <StatusTag key={s} status={s} />)}</div>
      <div data-component="Confidence" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Confidence value={0.62} previous={0.4} since="09:30" /><Confidence value={0.1} previous={0.3} muted />
      </div>
      <div data-component="Notice" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Notice>A note.</Notice><Notice tone="clamped">Window clamped to now.</Notice><Notice tone="empty">Nothing matched.</Notice><Notice tone="error">The record could not be read.</Notice>
      </div>
      <div data-component="ToolResult"><ToolResult tool="search_logs" result={RESULT} highlight={["timeout"]} /></div>
      <div data-component="EvidenceRecord"><EvidenceRecord record={{ handle: HANDLES[0]!, level: "error", source: "app logs", t_utc: "2026-01-01T09:12:40Z", host: "web-01", sampling: "1 in 10 per host", summary: "A job failed on a timeout.", raw: "2026-01-01T09:12:40.120Z ERROR [worker] job failed: timeout after 30s", raw_format: "text, one line" }} highlight={["timeout"]} /></div>
      <div data-component="ConflictPair"><ConflictPair conflict={{ question: "When did the errors start?", answers: [{ source: "Logs", value: "09:12 UTC", handle: HANDLES[0] }, { source: "Ticket", value: "09:20 UTC", note: "When the ticket was opened" }] }} /></div>
      <div data-component="StakeholderMessage"><StakeholderMessage audience="Executives" channel="#incidents" text="We are investigating failed jobs since 09:12 UTC. Next update at 10:00 UTC." asOf="09:30" nextUpdate="10:00" /></div>
      <Label>Label text</Label>
    </section>
  );
}

/** Every ported design-system component, in both themes side by side: for checking the port, and for the
 * accessibility suite. Not linked from the app. */
export default function DesignSystem() {
  return (
    <main style={{ display: "flex", flexWrap: "wrap", minHeight: "100vh" }}>
      <Pane theme="light" />
      <Pane theme="dark" />
    </main>
  );
}
