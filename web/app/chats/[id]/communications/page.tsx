import { notFound, redirect } from "next/navigation";
import { MessagesScreen } from "@/components/messages/MessagesScreen";
import { Shell } from "@/components/shell/Shell";
import { api, maybe } from "@/lib/api";
import type { ChatView, ImportedIncident, KbHit, MessagesView, TicketView } from "@/lib/types";
import { render, type WriteupData } from "@/lib/writeup/render";

/** The communications of a resolved chat, in the shell. */
export default async function Communications({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const chat = encodeURIComponent(id);
  const data = await maybe<WriteupData>(`/chats/${chat}/writeup`);
  if (!data) notFound();
  if (!data.resolution) redirect(`/chats/${id}?resolve`);
  const [incidents, view, chatView] = await Promise.all([api<ImportedIncident[]>("/incidents"), api<MessagesView>(`/chats/${chat}/messages`), api<ChatView>(`/chats/${chat}`)]);
  const number = chatView.chat.incident;
  const ticket = number ? await maybe<TicketView>(`/incident/ticket?number=${encodeURIComponent(number)}`) : null;
  const incident = incidents.find((i) => i.chat_id === id) ?? ticket?.incident ?? null;
  // The preview is a real search of the knowledge base for the entry's own terms (7.5).
  const terms = view.entry && data.writeup ? render(data, new Set(data.writeup.kept), data.resolution.note).terms : [];
  const hits = terms.length ? await api<KbHit[]>(`/kb?q=${encodeURIComponent(terms.join(" "))}`) : [];
  const res = data.resolution;
  return (
    <Shell here={{ step: "communications", chatId: id }} incidents={incidents}>
      <MessagesScreen chatId={id} data={data} view={view} hits={hits} incidentHandle={incident?.handle ?? null} head={{
        incident, ticket, title: chatView.chat.title,
        meta: incident ? `${incident.number}${incident.service ? ` · ${incident.service}` : ""} · resolved ${res.resolved_at} UTC by ${res.resolved_by}` : `Resolved ${res.resolved_at} UTC by ${res.resolved_by}`,
      }} />
    </Shell>
  );
}
