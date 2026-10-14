import { notFound, redirect } from "next/navigation";
import { IncidentBar } from "@/components/chat/IncidentBar";
import { AppBar } from "@/components/shell/AppBar";
import { SummaryScreen } from "@/components/summary/SummaryScreen";
import { api, maybe } from "@/lib/api";
import type { IncidentCard } from "@/lib/types";
import type { WriteupData } from "@/lib/writeup/render";

export default async function Summary({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await maybe<WriteupData>(`/chats/${encodeURIComponent(id)}/writeup`);
  if (!data) notFound();
  if (!data.resolution) redirect(`/chats/${id}?resolve`);
  const card = await api<IncidentCard>("/incident");
  return (
    <SummaryScreen chatId={id} data={data}>
      <AppBar><IncidentBar card={card} chatId={id} current="summary" resolved /></AppBar>
    </SummaryScreen>
  );
}
