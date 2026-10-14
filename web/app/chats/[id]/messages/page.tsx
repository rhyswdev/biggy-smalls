import { notFound, redirect } from "next/navigation";
import { IncidentBar } from "@/components/chat/IncidentBar";
import { MessagesScreen } from "@/components/messages/MessagesScreen";
import { AppBar } from "@/components/shell/AppBar";
import { api, maybe } from "@/lib/api";
import type { IncidentCard, KbHit, MessagesView } from "@/lib/types";
import { render, type WriteupData } from "@/lib/writeup/render";

export default async function Messages({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const chat = encodeURIComponent(id);
  const data = await maybe<WriteupData>(`/chats/${chat}/writeup`);
  if (!data) notFound();
  if (!data.resolution) redirect(`/chats/${id}?resolve`);
  const [card, view] = await Promise.all([api<IncidentCard>("/incident"), api<MessagesView>(`/chats/${chat}/messages`)]);
  // The preview is a real search of the knowledge base for the entry's own terms (7.5).
  const terms = view.entry && data.writeup ? render(data, new Set(data.writeup.kept), data.resolution.note).terms : [];
  const hits = terms.length ? await api<KbHit[]>(`/kb?q=${encodeURIComponent(terms.join(" "))}`) : [];
  return (
    <MessagesScreen chatId={id} data={data} view={view} hits={hits} incidentHandle={card.incident?.handle ?? null}>
      <AppBar><IncidentBar card={card} chatId={id} current="messages" resolved /></AppBar>
    </MessagesScreen>
  );
}
