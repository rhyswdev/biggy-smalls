/** Text with the given terms marked. The text is split into strings and <mark> elements: it is never parsed as
 * HTML or Markdown, because evidence and chat are untrusted. */
export function Marked({ text, terms }: { text: string; terms?: readonly string[] | null }) {
  const wanted = (terms ?? []).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!text || !wanted.length) return <>{text}</>;
  const parts = text.split(new RegExp(`(${wanted.join("|")})`, "gi"));
  return <>{parts.map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part))}</>;
}
