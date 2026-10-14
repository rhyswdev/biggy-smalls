import { AgentPanel } from "@/components/dashboard/AgentPanel";
import { ChatsList } from "@/components/dashboard/ChatsList";
import { IncidentCard } from "@/components/dashboard/IncidentCard";
import { KnowledgeBand } from "@/components/dashboard/KnowledgeBand";
import { AppBar } from "@/components/shell/AppBar";
import { api } from "@/lib/api";
import { fold } from "@/lib/chat/reduce";
import type { Capabilities, ChatSummary, ChatView, IncidentCard as Card } from "@/lib/types";

export default async function Dashboard() {
  const [caps, card, chats] = await Promise.all([api<Capabilities>("/capabilities"), api<Card>("/incident"), api<ChatSummary[]>("/chats")]);
  const agent = chats.find((c) => c.origin === "headless") ?? null;      // the latest "Run agent" chat drives the panel
  const view = agent ? await api<ChatView>(`/chats/${agent.id}`) : null;
  const resolved = chats.some((c) => c.status === "resolved" && c.origin !== "past");
  return (
    <div className="v-dash" style={{ minHeight: "100vh" }}>
      <AppBar current="dashboard" />
      <main style={{ maxWidth: 1160, margin: "0 auto", padding: "28px 24px 64px", display: "flex", flexDirection: "column", gap: 32, boxSizing: "border-box" }}>
        <section aria-labelledby="inc-h" className="sec">
          <div className="sec-head">
            <h2 id="inc-h" className="title" style={{ margin: 0 }}>Incidents</h2>
            <span className="small muted">Pushed from ServiceNow when a work note is added</span>
          </div>
          <IncidentCard card={card} resolved={resolved}>
            <AgentPanel chatId={agent?.id ?? null} initial={view ? fold(view.events) : null} author={card.note.author} noteTime={card.note.time}
              recorded={caps.last_recorded} profile={caps.profile} mode={caps.mode} />
          </IncidentCard>
        </section>
        <section aria-labelledby="chats-h" className="sec">
          <div className="sec-head">
            <h2 id="chats-h" className="title" style={{ margin: 0 }}>Chats</h2>
            <span className="small muted">Each chat keeps every read, decision and reason</span>
          </div>
          <ChatsList chats={chats} />
        </section>
        <KnowledgeBand />
      </main>
    </div>
  );
}
