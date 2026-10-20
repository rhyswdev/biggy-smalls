import { cookies } from "next/headers";
import Link from "next/link";
import { AgentPanel } from "@/components/dashboard/AgentPanel";
import { FirstRun } from "@/components/firstrun/FirstRun";
import { IncidentHeader } from "@/components/shell/IncidentHeader";
import { Shell } from "@/components/shell/Shell";
import { StatusBanner } from "@/components/shell/StatusBanner";
import { api, maybe } from "@/lib/api";
import { fold, leading } from "@/lib/chat/reduce";
import { clock } from "@/lib/format";
import type { Capabilities, ChatView, ImportedIncident, OpenIncidents, TicketView } from "@/lib/types";

/** `/`: the first-run screen until an incident is imported, then the overview of the latest one. */
export default async function Home() {
  const incidents = await api<ImportedIncident[]>("/incidents");
  if (!incidents.length) {
    const [open, theme] = await Promise.all([api<OpenIncidents>("/incidents/open"), cookies().then((c) => c.get("smalls-theme")?.value ?? "system")]);
    return <FirstRun open={open} theme={theme} />;
  }
  const incident = incidents[0]!;
  const [caps, view, ticket] = await Promise.all([
    api<Capabilities>("/capabilities"), api<ChatView>(`/chats/${incident.chat_id}`),
    maybe<TicketView>(`/incident/ticket?number=${encodeURIComponent(incident.number)}`),
  ]);
  const state = fold(view.events);
  const cause = leading(state.report);
  const due = incident.next_update_due ? `Next update due ${clock(incident.next_update_due)} UTC.` : "";
  const running = view.chat.status === "running";
  const banner = running ? (
    <StatusBanner tone="action" title="Diagnosis in progress." text={`Smalls started automatically when the ticket was imported. ${due}`.trim()} />
  ) : view.chat.status === "stopped" ? (
    <StatusBanner tone="action" title="Diagnosis stopped." text={`Start it again from the Smalls panel. ${due}`.trim()} />
  ) : cause ? (
    <StatusBanner tone="action" glyph="●" title="Probable cause identified." text={`${cause.statement} ${due}`.trim()}>
      <Link className="lbtn primary" href={`/chats/${incident.chat_id}`}>Open diagnosis</Link>
    </StatusBanner>
  ) : (
    <StatusBanner tone="neutral" title="No probable cause yet." text={due}>
      <Link className="lbtn" href={`/chats/${incident.chat_id}`}>Open diagnosis</Link>
    </StatusBanner>
  );
  return (
    <Shell here={{ step: "overview" }} incidents={incidents}>
      <IncidentHeader incident={incident} ticket={ticket} resolved={incident.status === "resolved"} banner={banner} />
      <main className="cols">
        <div className="maincol" />
        <aside className="rail" aria-label="Smalls diagnosis">
          <section className="card pad v-dash" aria-labelledby="ag-h">
            <h2 id="ag-h" className="c-h" style={{ marginBottom: 10 }}>Smalls diagnosis</h2>
            <div className="inc-side-in">
              <AgentPanel chatId={incident.chat_id} initial={state} author={null} noteTime={null} recorded={caps.last_recorded} profile={caps.profile} mode={caps.mode} />
            </div>
          </section>
        </aside>
      </main>
    </Shell>
  );
}
