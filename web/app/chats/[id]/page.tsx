import { notFound } from "next/navigation";
import { ChatScreen } from "@/components/chat/ChatScreen";
import type { ToolSchema } from "@/components/chat/Gate";
import { Shell } from "@/components/shell/Shell";
import { api, maybe } from "@/lib/api";
import { fold } from "@/lib/chat/reduce";
import type { ChatView, ImportedIncident, TicketView } from "@/lib/types";

/** The diagnosis of one chat, in the shell. */
export default async function Chat({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const view = await maybe<ChatView>(`/chats/${encodeURIComponent(id)}`);
  if (!view) notFound();
  const chat = view.chat;
  const [incidents, schemas, ticket] = await Promise.all([
    api<ImportedIncident[]>("/incidents"), api<ToolSchema[]>("/tools/schemas"),
    chat.incident ? maybe<TicketView>(`/incident/ticket?number=${encodeURIComponent(chat.incident)}`) : Promise.resolve(null),
  ]);
  const mine = incidents.find((i) => i.chat_id === chat.id) ?? null;
  const state = fold(view.events);        // the first paint: the same fold the browser carries on live
  // Resolving needs a report and an incident: the summary is written from the one and is about the other.
  const resolvable = chat.origin !== "blank" && chat.origin !== "past";
  return (
    <Shell here={{ step: "diagnosis", chatId: chat.id }} incidents={incidents}>
      <ChatScreen chat={chat} initial={state} schemas={schemas} incident={mine ?? ticket?.incident ?? null} ticket={ticket} imported={Boolean(mine)}
        nextUpdate={mine?.next_update_due ?? null} resolvable={resolvable} />
    </Shell>
  );
}
