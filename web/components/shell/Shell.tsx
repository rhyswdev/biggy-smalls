import { cookies } from "next/headers";
import { api } from "@/lib/api";
import type { ChatSummary, ImportedIncident, KbRow, OpenIncidents } from "@/lib/types";
import { Sidebar, type Here } from "./Sidebar";

/** The two-column shell (v4): the sidebar, then the page. Below 820 px the sidebar stacks above the page. A
 * server component each page wraps itself in, because the sidebar marks where the page is and a layout does not
 * know its route. */
export async function Shell({ here, incidents, children }: { here: Here; incidents?: ImportedIncident[]; children: React.ReactNode }) {
  const [list, chats, kb, open, theme] = await Promise.all([
    incidents ?? api<ImportedIncident[]>("/incidents"), api<ChatSummary[]>("/chats"), api<KbRow[]>("/kb"), api<OpenIncidents>("/incidents/open"),
    cookies().then((c) => c.get("smalls-theme")?.value ?? "system"),
  ]);
  return (
    <div className="v-shell">
      <div className="shell">
        <div className="side"><Sidebar incidents={list} chats={chats} kb={kb} open={open} here={here} theme={theme} /></div>
        <div className="page">{children}</div>
      </div>
    </div>
  );
}
