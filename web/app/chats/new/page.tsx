import { IncidentBar } from "@/components/chat/IncidentBar";
import { NewChat } from "@/components/chat/NewChat";
import { AppBar } from "@/components/shell/AppBar";
import { api } from "@/lib/api";
import type { IncidentCard } from "@/lib/types";

/** Before anything is sent: a new, empty chat, or ("Investigate in chat") the incident with the commander's note
 * loaded into the message box. Nothing runs until the person sends. */
export default async function New({ searchParams }: { searchParams: Promise<{ from?: string }> }) {
  const fromIncident = (await searchParams).from === "incident";
  const card = fromIncident ? await api<IncidentCard>("/incident") : null;
  return (
    <div className="v-chat" style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppBar><IncidentBar card={card} chatId={null} current="investigate" resolved={false} blank={!fromIncident} /></AppBar>
      <main aria-label="Conversation" style={{ maxWidth: 920, width: "100%", boxSizing: "border-box" }}>
        <div className="convo">
          {fromIncident && card ? (
            <section aria-label="Start" className="intro">
              <span className="label">{[card.incident?.number, "Incident"].filter(Boolean).join(" · ")}</span>
              <h1 className="intro-h">{card.incident?.short_description ?? "The incident"}</h1>
              <p className="narr muted">The incident commander’s message is loaded below, as it arrived{card.note.time ? ` at ${card.note.time}` : ""}. Send it as it is or edit it first. Smalls shows each read it wants to run and waits for your approval.</p>
            </section>
          ) : (
            <section aria-label="Start" className="intro">
              <h1 className="intro-h">New chat</h1>
              <p className="narr muted">Describe what you’re seeing, or paste a ticket number to start from. Smalls shows each read it wants to run and waits for your approval.</p>
            </section>
          )}
          <NewChat origin={fromIncident ? "chat" : "blank"} draft={fromIncident && card ? card.trigger : ""} />
        </div>
      </main>
    </div>
  );
}
