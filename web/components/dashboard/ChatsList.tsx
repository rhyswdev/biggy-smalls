import Link from "next/link";
import { StatusTag } from "@/components/ds";
import { clock, day, plural } from "@/lib/format";
import type { ChatSummary } from "@/lib/types";

const ORIGIN: Record<string, string> = { chat: "investigated in chat", headless: "agent run", blank: "new chat", past: "recorded run" };

function status(c: ChatSummary): React.ReactNode {
  switch (c.status) {
    case "waiting": return <StatusTag status="awaiting_approval">Waiting on {plural(c.pending, "read")}</StatusTag>;
    case "running": return <StatusTag status="running" />;
    case "resolved": return <StatusTag status="resolved" />;
    case "stopped": return <StatusTag status="skipped">Stopped</StatusTag>;
    case "failed": return <StatusTag status="error">Failed</StatusTag>;
    default: return <StatusTag status="ok">Finished</StatusTag>;
  }
}

/** Every chat, newest first. One waiting on the commander is the only one with a primary button. */
export function ChatsList({ chats }: { chats: ChatSummary[] }) {
  if (!chats.length) return <p className="small muted" style={{ margin: 0 }}>No chats yet. Investigate in chat or run the agent to start one.</p>;
  return (
    <ul className="card list">
      {chats.map((c) => {
        const t = c.tally;
        const meta = [c.incident, ORIGIN[c.origin], c.origin === "past" && c.recorded ? `made ${day(c.recorded)}` : null,
          t.run || t.rejected || t.skipped ? [t.run ? `${plural(t.run, "read")} run` : "", t.rejected ? `${t.rejected} rejected` : "", t.skipped ? `${t.skipped} skipped` : ""].filter(Boolean).join(", ") : null]
          .filter(Boolean).join(" · ");
        return (
          <li key={c.id} className={`chat-row${c.origin === "past" ? " past" : ""}`}>
            <span className="chat-status">{status(c)}</span>
            <div className="chat-main">
              <span className="chat-title">{c.title}</span>
              <span className="chat-meta">{meta}</span>
            </div>
            <span className="time muted chat-when">{day(c.now)} {clock(c.now)}</span>
            <Link className={`lbtn${c.status === "waiting" ? " primary" : ""}`} href={`/chats/${c.id}${c.status === "waiting" ? "#pending" : ""}`}>
              {c.status === "waiting" ? "Resume" : "Open"}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
