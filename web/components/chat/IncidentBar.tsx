import Link from "next/link";
import { StatusTag } from "@/components/ds";
import { priority } from "@/lib/format";
import type { IncidentCard } from "@/lib/types";

export type Step = "investigate" | "summary" | "messages";

/** The incident bar and the three steps: Investigate → Summary → Messages. Before the incident is resolved the
 * later steps are plain text; after, every step is a link and a done one shows ✓ (section 4). */
export function IncidentBar({ card, chatId, current, resolved, blank, children }: {
  card: IncidentCard | null; chatId: string | null; current: Step; resolved: boolean; blank?: boolean; children?: React.ReactNode;
}) {
  const inc = card?.incident;
  const prio = priority(inc?.priority);
  const steps: [Step, string][] = [["investigate", "Investigate"], ["summary", "Summary"], ["messages", "Messages"]];
  const href = (s: Step) => (s === "investigate" ? `/chats/${chatId}` : `/chats/${chatId}/${s}`);
  return (
    <>
      <span className="vr" aria-hidden="true" />
      <div className="inc">
        {blank || !inc ? (
          <>
            <span className="inc-title">New chat</span>
            <span className="small muted">Not linked to an incident yet</span>
          </>
        ) : (
          <>
            <div className="inc-line">
              {prio ? <span className="prio" title={`Priority ${inc.priority}`}>{prio.code || prio.text}</span> : null}
              <span className="inc-title">{inc.short_description}</span>
              {resolved ? <StatusTag status="resolved" /> : <StatusTag status="running">Investigating</StatusTag>}
            </div>
            <span className="time" style={{ color: "var(--ink-muted)" }}>
              {[inc.number, inc.service, card?.note.author ? `IC ${card.note.author}` : null, inc.opened ? `opened ${inc.opened.slice(11, 16)} UTC` : null].filter(Boolean).join(" · ")}
            </span>
          </>
        )}
      </div>
      {!blank && chatId ? (
        <nav aria-label="Incident steps">
          <ol className="steps">
            {steps.map(([s, label], i) => {
              const done = resolved && steps.findIndex(([x]) => x === current) > i;
              const body = <><span className="step-n">{done ? "✓" : i + 1}</span>{label}</>;
              if (s === current) return <li key={s}><span className="step" aria-current="step">{body}</span></li>;
              if (!resolved && s !== "investigate") return <li key={s}><span className="step" title="Opens once the incident is marked resolved">{body}</span></li>;
              return <li key={s}><Link className="step" href={href(s)}>{body}</Link></li>;
            })}
          </ol>
        </nav>
      ) : null}
      {children}
    </>
  );
}
