import { NewChat } from "@/components/chat/NewChat";
import { Shell } from "@/components/shell/Shell";
import { api } from "@/lib/api";
import type { ImportedIncident, IncidentCard } from "@/lib/types";

/** A new chat: the sidebar hidden, one centred composer. `?from=incident` loads the commander's note to send as
 * it is. Nothing runs until the person sends. */
export default async function New({ searchParams }: { searchParams: Promise<{ from?: string }> }) {
  const fromIncident = (await searchParams).from === "incident";
  const [incidents, card] = await Promise.all([api<ImportedIncident[]>("/incidents"), fromIncident ? api<IncidentCard>("/incident") : Promise.resolve(null)]);
  const linked = card?.incident?.number && incidents.some((i) => i.number === card.incident!.number) ? card.incident.number : null;
  return (
    <Shell here={{ newChat: true }} incidents={incidents} collapsed>
      <div className="v-diag" style={{ height: "auto", flex: "1 1 auto" }}>
        <main aria-label="Conversation" className="mainc" style={{ overflow: "visible" }}>
          <NewChat draft={card ? card.trigger : ""} linked={linked} incidents={incidents.map((i) => ({ number: i.number, title: i.short_description ?? i.number }))} />
        </main>
      </div>
    </Shell>
  );
}
