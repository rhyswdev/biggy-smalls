"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { importIncident } from "@/lib/actions/incidents";
import type { OpenIncidents } from "@/lib/types";

/** The open P1 and P2 tickets for the person's groups, as rows: one not yet in Smalls imports on a click, one
 * already imported opens its overview. */
export function OpenTickets({ incidents, only }: { incidents: OpenIncidents["incidents"]; only?: "new" }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const rows = only === "new" ? incidents.filter((i) => !i.imported) : incidents;
  if (!rows.length) return null;
  return (
    <>
      {rows.map((i) => {
        const inner = (
          <>
            <span className="prio">{i.priority_code ? `P${i.priority_code}` : "P?"}</span>
            <span style={{ fontWeight: 600, flex: "1 1 180px", textAlign: "left" }}>{i.short_description ?? i.number}</span>
            <span className="small muted">{i.number} · {i.imported ? "already imported" : "not yet in Smalls"}</span>
          </>
        );
        return i.imported ? (
          <Link key={i.number} className="recent" href="/">{inner}</Link>
        ) : (
          <button key={i.number} type="button" className="recent" disabled={pending}
            onClick={() => start(async () => { setError(null); const r = await importIncident(i.number); if (r && !r.ok) setError(r.error ?? "The incident could not be imported."); })}>
            {inner}
          </button>
        );
      })}
      {error ? <span role="alert" className="small" style={{ color: "var(--danger)" }}>{error}</span> : null}
    </>
  );
}
