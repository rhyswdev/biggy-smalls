"use client";

import { Handle } from "@/components/ds";

// A handle inside prose: a prefix, a colon, then no space. A web address is not one.
const HANDLE = /\b([a-z][a-z0-9_]*:(?!\/\/)[A-Za-z0-9][^\s,;()[\]{}"']*[A-Za-z0-9#\]}]|ic:note\/[0-9a-f]{8})/g;

/** Prose with each handle in it made a button that opens the record. The text is split into strings and
 * buttons; it is never parsed as HTML or Markdown. */
export function Linked({ text, onOpen }: { text: string; onOpen: (h: string) => void }) {
  const out: React.ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(HANDLE)) {
    const i = m.index ?? 0;
    if (i > at) out.push(text.slice(at, i));
    out.push(<Handle key={`${i}-${m[0]}`} id={m[0]} onOpen={onOpen} />);
    at = i + m[0].length;
  }
  if (at < text.length) out.push(text.slice(at));
  return <>{out}</>;
}
