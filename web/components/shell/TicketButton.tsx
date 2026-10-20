"use client";

import { useEffect, useRef, useState } from "react";
import type { TicketView as Ticket } from "@/lib/types";
import { TicketView } from "./TicketView";

/** "View ticket": opens the raw ticket in a drawer on the right. Escape or × closes it, and focus goes back. */
export function TicketButton({ ticket }: { ticket: Ticket }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) panel.current?.focus(); }, [open]);
  const close = () => { setOpen(false); button.current?.focus(); };
  return (
    <>
      <button ref={button} type="button" className="tlink" aria-expanded={open} onClick={() => setOpen(!open)}>View ticket</button>
      {open ? (
        <div ref={panel} className="v-drawer" role="dialog" aria-modal="false" aria-labelledby="ticket-h" tabIndex={-1}
          onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); close(); } }}>
          <TicketView ticket={ticket} onClose={close} headingId="ticket-h" />
        </div>
      ) : null}
    </>
  );
}
