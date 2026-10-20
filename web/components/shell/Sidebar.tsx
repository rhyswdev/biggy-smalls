import Link from "next/link";
import { clock, day } from "@/lib/format";
import type { ChatSummary, ImportedIncident, KbRow, OpenIncidents } from "@/lib/types";
import { NewIncidentMenu } from "./NewIncidentMenu";
import { ThemeSwitch } from "./ThemeSwitch";

export type Step = "overview" | "diagnosis" | "resolution" | "communications";
export interface Here { step?: Step; chatId?: string; newChat?: boolean }

/** The sidebar (SidebarV4): the brand, New incident, the imported incident with its four steps (Resolution and
 * Communications wait "after resolving"), other chats, and Earlier: resolved chats, saved knowledge articles
 * and recorded runs. The first imported incident is the one `/` shows; any other is a row to its diagnosis. */
export function Sidebar({ incidents, chats, kb, open, here, theme }: {
  incidents: ImportedIncident[]; chats: ChatSummary[]; kb: KbRow[]; open: OpenIncidents; here: Here; theme: string;
}) {
  const [main, ...others] = incidents;
  const mine = new Set(incidents.map((i) => i.chat_id));
  const live = chats.filter((c) => !mine.has(c.id) && c.origin !== "past" && c.status !== "resolved");
  const saved = new Set(kb.map((k) => k.chat_id));
  const earlier = [
    ...chats.filter((c) => !mine.has(c.id) && c.origin !== "past" && c.status === "resolved")
      .map((c) => ({ key: c.id, href: `/chats/${c.id}`, title: c.title, meta: saved.has(c.id) ? "Resolved · in the knowledge base" : "Resolved" })),
    ...kb.filter((k) => !chats.some((c) => c.id === k.chat_id && c.origin !== "past"))
      .map((k) => ({ key: `kb-${k.id}`, href: `/chats/${k.chat_id}`, title: k.title, meta: `Saved ${day(k.saved_at)} · in the knowledge base` })),
    ...chats.filter((c) => c.origin === "past")
      .map((c) => ({ key: c.id, href: `/chats/${c.id}`, title: c.title, meta: `Recorded run${c.recorded ? ` · ${day(c.recorded)}` : ""}` })),
  ];
  return (
    <div className="v-sb" style={{ height: "100%" }}><nav aria-label="Smalls" className="sb">
      <span style={{ display: "flex", alignItems: "center" }}>
        <Link className="sb-brand" href="/">Smalls</Link>
        <ThemeSwitch theme={theme} />
      </span>
      <NewIncidentMenu open={open} />
      {main ? <IncidentSteps incident={main} here={here} /> : null}
      {others.length ? (
        <div className="sb-sec">
          <span className="sb-lab">Other incidents</span>
          <ol className="sb-items" style={{ marginLeft: 0, borderLeft: 0, paddingLeft: 0 }}>
            {others.map((i) => (
              <li key={i.number}><Link className="sb-it" href={`/chats/${i.chat_id}`} aria-current={here.chatId === i.chat_id ? "page" : undefined}>
                <span className="sb-it-t">{i.short_description ?? i.number}</span><span className="sb-lock">{i.number}</span>
              </Link></li>
            ))}
          </ol>
        </div>
      ) : null}
      {live.length || here.newChat ? (
        <div className="sb-sec">
          <span className="sb-lab">Chats</span>
          <ol className="sb-items" style={{ marginLeft: 0, borderLeft: 0, paddingLeft: 0 }}>
            {here.newChat ? <li><Link className="sb-it" href="/chats/new" aria-current="page"><span className="sb-it-t">New chat</span><span className="sb-lock">not linked yet</span></Link></li> : null}
            {live.map((c) => (
              <li key={c.id}><Link className="sb-it" href={`/chats/${c.id}`} aria-current={here.chatId === c.id ? "page" : undefined}>
                <span className="sb-it-t">{c.title}</span>{c.incident ? null : <span className="sb-lock">not linked yet</span>}
              </Link></li>
            ))}
          </ol>
        </div>
      ) : null}
      {earlier.length ? (
        <div className="sb-sec">
          <span className="sb-lab">Earlier</span>
          {earlier.map((e) => (
            <Link key={e.key} className="sb-past" href={e.href}><b>{e.title}</b><span className="sb-meta">{e.meta}</span></Link>
          ))}
        </div>
      ) : null}
    </nav></div>
  );
}

function IncidentSteps({ incident, here }: { incident: ImportedIncident; here: Here }) {
  const resolved = incident.status === "resolved";
  const at = (s: Step) => (here.step === s && (s === "overview" || here.chatId === incident.chat_id) ? "page" : undefined);
  const label = resolved ? "Resolved today" : incident.major ? "Open major incident" : "Open incident";
  const meta = resolved && incident.resolution ? `${incident.number} · resolved ${incident.resolution.resolved_at} UTC` : `${incident.number} · impact since ${clock(incident.opened)} UTC`;
  return (
    <div className="sb-sec">
      <span className="sb-lab">{label}</span>
      <div className="sb-inc">
        <span className="sb-inc-top">{incident.priority_code ? <span className="prio">P{incident.priority_code}</span> : null}<span className="sb-inc-t">{incident.short_description ?? incident.number}</span></span>
        <span className="sb-meta">{meta}</span>
      </div>
      <ol className="sb-items">
        <li><Link className="sb-it" href="/" aria-current={at("overview")}><span className="sb-it-t">Overview</span></Link></li>
        <li><Link className="sb-it" href={`/chats/${incident.chat_id}`} aria-current={at("diagnosis")}><span className="sb-it-t">Diagnosis</span>
          {incident.status === "running" ? <span className="sb-lock"><span className="v-spin" aria-hidden="true" />running</span> : incident.status === "waiting" ? <span className="sb-lock">waiting on you</span> : null}</Link></li>
        {resolved ? (
          <>
            <li><Link className="sb-it" href={`/chats/${incident.chat_id}/resolution`} aria-current={at("resolution")}><span className="sb-it-t">Resolution</span></Link></li>
            <li><Link className="sb-it" href={`/chats/${incident.chat_id}/messages`} aria-current={at("communications")}><span className="sb-it-t">Communications</span></Link></li>
          </>
        ) : (
          <>
            <li><span className="sb-it off" aria-disabled="true"><span className="sb-it-t">Resolution</span><span className="sb-lock">after resolving</span></span></li>
            <li><span className="sb-it off" aria-disabled="true"><span className="sb-it-t">Communications</span><span className="sb-lock">after resolving</span></span></li>
          </>
        )}
      </ol>
    </div>
  );
}
