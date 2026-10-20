import { cookies } from "next/headers";
import Link from "next/link";
import { FirstRun } from "@/components/firstrun/FirstRun";
import { DiagnosisPanel } from "@/components/overview/DiagnosisPanel";
import { CiMap, Contacts, Glossary, Theories, Timeline, WhatsGoingOn } from "@/components/overview/Sections";
import { IncidentHeader } from "@/components/shell/IncidentHeader";
import { Shell } from "@/components/shell/Shell";
import { StatusBanner } from "@/components/shell/StatusBanner";
import { api, maybe } from "@/lib/api";
import { fold, leading } from "@/lib/chat/reduce";
import { clock, confidenceLabel } from "@/lib/format";
import type { ChatView, ImportedIncident, OpenIncidents, Overview, TicketView } from "@/lib/types";

/** `/`: the first-run screen until an incident is imported, then the overview of the latest one. */
export default async function Home() {
  const incidents = await api<ImportedIncident[]>("/incidents");
  if (!incidents.length) {
    const [open, theme] = await Promise.all([api<OpenIncidents>("/incidents/open"), cookies().then((c) => c.get("smalls-theme")?.value ?? "system")]);
    return <FirstRun open={open} theme={theme} />;
  }
  const incident = incidents[0]!;
  const [view, ticket, overview] = await Promise.all([
    api<ChatView>(`/chats/${incident.chat_id}`),
    maybe<TicketView>(`/incident/ticket?number=${encodeURIComponent(incident.number)}`),
    maybe<Overview>(`/incident/overview?number=${encodeURIComponent(incident.number)}`),
  ]);
  const impactStart = overview?.timeline.find((t) => t.impact_start)?.time ?? null;
  const state = fold(view.events);
  const cause = leading(state.report);
  const action = state.report ? [...state.report.actions].sort((a, b) => a.rank - b.rank)[0] : undefined;
  const due = incident.next_update_due ? `Next update due ${clock(incident.next_update_due)} UTC.` : "";
  const running = view.chat.status === "running";
  const waiting = view.chat.status === "waiting";
  const banner = waiting ? (
    <StatusBanner tone="attention" title="Waiting on you." text={`Checks need your approval before diagnosis can continue. ${due}`.trim()}>
      <Link className="lbtn primary" href={`/chats/${incident.chat_id}#pending`}>Go to approvals</Link>
    </StatusBanner>
  ) : running ? (
    <StatusBanner tone="action" title="Diagnosis in progress." text={`Smalls started automatically when the ticket was imported. ${due}`.trim()} />
  ) : view.chat.status === "stopped" ? (
    <StatusBanner tone="action" title="Diagnosis stopped." text={`Start it again from the Smalls panel. ${due}`.trim()} />
  ) : cause && cause.confidence >= 0.7 ? (
    <StatusBanner tone="action" glyph="●" title="Probable cause identified."
      text={`${cause.statement} Confidence: ${confidenceLabel(cause.confidence)}.${action ? ` Recommended action: ${action.action}` : ""} ${due}`.trim()}>
      <Link className="lbtn primary" href={`/chats/${incident.chat_id}`}>Open diagnosis</Link>
    </StatusBanner>
  ) : (
    <StatusBanner tone="neutral" title="No probable cause yet." text={`${cause ? `${cause.statement} is ahead at ${confidenceLabel(cause.confidence)}. ` : ""}${due}`.trim()}>
      <Link className="lbtn" href={`/chats/${incident.chat_id}`}>Open diagnosis</Link>
    </StatusBanner>
  );
  return (
    <Shell here={{ step: "overview" }} incidents={incidents}>
      <IncidentHeader incident={incident} ticket={ticket} resolved={incident.status === "resolved"} banner={banner}
        impactStart={impactStart ? `${incident.opened.slice(0, 11)}${impactStart}:00Z` : null} />
      <div className="cols">
        <main className="maincol" aria-label="Incident overview">
          {overview ? (
            <>
              <WhatsGoingOn incident={incident} overview={overview} />
              <CiMap incident={incident} overview={overview} />
              <Theories overview={overview} />
              <Timeline overview={overview} />
              <Glossary overview={overview} />
            </>
          ) : <p className="small muted">The overview is written when the incident is imported.</p>}
        </main>
        <aside className="rail" aria-label="Smalls diagnosis and contacts">
          <DiagnosisPanel chatId={incident.chat_id} initial={state} />
          {overview ? <Contacts overview={overview} /> : null}
        </aside>
      </div>
    </Shell>
  );
}
