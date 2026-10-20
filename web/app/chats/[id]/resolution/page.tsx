import { notFound, redirect } from "next/navigation";
import { Shell } from "@/components/shell/Shell";
import { SummaryScreen } from "@/components/summary/SummaryScreen";
import { api, maybe } from "@/lib/api";
import type { ChatView, ImportedIncident, TicketView } from "@/lib/types";
import type { WriteupData } from "@/lib/writeup/render";

/** The resolution review of a resolved chat, in the shell. */
export default async function Resolution({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await maybe<WriteupData>(`/chats/${encodeURIComponent(id)}/writeup`);
  if (!data) notFound();
  if (!data.resolution) redirect(`/chats/${id}?resolve`);
  const [incidents, view] = await Promise.all([api<ImportedIncident[]>("/incidents"), api<ChatView>(`/chats/${encodeURIComponent(id)}`)]);
  const number = view.chat.incident;
  const ticket = number ? await maybe<TicketView>(`/incident/ticket?number=${encodeURIComponent(number)}`) : null;
  const incident = incidents.find((i) => i.chat_id === id) ?? ticket?.incident ?? null;
  const res = data.resolution;
  return (
    <Shell here={{ step: "resolution", chatId: id }} incidents={incidents}>
      <SummaryScreen chatId={id} data={data} head={{
        incident, ticket, title: view.chat.title,
        meta: incident ? `${incident.number}${incident.service ? ` · ${incident.service}` : ""} · resolved ${res.resolved_at} UTC by ${res.resolved_by}` : `Resolved ${res.resolved_at} UTC by ${res.resolved_by}`,
      }} />
    </Shell>
  );
}
