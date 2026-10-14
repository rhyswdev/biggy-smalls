import Link from "next/link";
import { notFound } from "next/navigation";
import { ChatScreen } from "@/components/chat/ChatScreen";
import type { ToolSchema } from "@/components/chat/Gate";
import { IncidentBar } from "@/components/chat/IncidentBar";
import { AppBar } from "@/components/shell/AppBar";
import { api, maybe } from "@/lib/api";
import { fold } from "@/lib/chat/reduce";
import type { ChatView, IncidentCard } from "@/lib/types";

export default async function Chat({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const view = await maybe<ChatView>(`/chats/${encodeURIComponent(id)}`);
  if (!view) notFound();
  const [card, schemas] = await Promise.all([api<IncidentCard>("/incident"), api<ToolSchema[]>("/tools/schemas")]);
  const chat = view.chat;
  const blank = chat.origin === "blank";
  const resolved = chat.status === "resolved";
  const state = fold(view.events);        // the first paint: the same fold the browser carries on live
  return (
    <ChatScreen chat={chat} initial={state} schemas={schemas} card={blank ? null : card} resolved={resolved}>
      <AppBar>
        <IncidentBar card={blank ? null : card} chatId={chat.id} current="investigate" resolved={resolved} blank={blank}>
          {!blank && chat.origin !== "past" && !resolved ? <Link className="lbtn" href={`/chats/${chat.id}?resolve`} scroll={false}>Mark resolved…</Link> : null}
        </IncidentBar>
      </AppBar>
    </ChatScreen>
  );
}
