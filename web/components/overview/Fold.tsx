"use client";

import { useState } from "react";

/** A card whose body folds away, closed at first, with a one-line summary beside its heading. */
export function Fold({ id, title, sub, children }: { id: string; title: string; sub: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card pad" aria-labelledby={id}>
      <button type="button" className="sec-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="chev" aria-hidden="true">›</span><h2 id={id} className="c-h" style={{ margin: 0 }}>{title}</h2><span className="c-sub">{sub}</span>
      </button>
      {open ? children : null}
    </section>
  );
}
