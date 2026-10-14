// The markdown the model writes in its prose (Q7 in ui-build.md): a small subset read into blocks and runs, which
// components build into elements. Paragraphs, line breaks, bullet and numbered lists, headings (shown as a bold
// line), bold and inline code. Nothing is parsed as HTML, and any other markdown stays as the characters it was
// written with. Pure.

export interface Run { text: string; bold?: boolean; code?: boolean }
export type Line = Run[];
export type Block =
  | { kind: "p" | "h"; lines: Line[] }
  | { kind: "ul"; items: Line[][] }
  | { kind: "ol"; items: Line[][]; start: number };

const BULLET = /^\s*[-*+•]\s+(.*)$/;
const NUMBER = /^\s*(\d{1,9})[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*?)(?:\s+#+)?\s*$/;
const RULE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
// A code span, or bold that opens and closes on a non-space; both stay inside one line.
const INLINE = /`([^`\n]+)`|\*\*(?=\S)([^\n]*?\S)\*\*/g;

export function blocks(text: string): Block[] {
  const out: Block[] = [];
  let open: Block | null = null;      // the block a plain line continues
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim() || RULE.test(line)) { open = null; continue; }
    const h = HEADING.exec(line);
    if (h) { out.push({ kind: "h", lines: [runs(h[1]!)] }); open = null; continue; }
    const b = BULLET.exec(line);
    if (b) {
      if (open?.kind !== "ul") out.push((open = { kind: "ul", items: [] }));
      open.items.push([runs(b[1]!)]);
      continue;
    }
    const n = NUMBER.exec(line);
    if (n) {
      if (open?.kind !== "ol") out.push((open = { kind: "ol", items: [], start: Number(n[1]) }));
      open.items.push([runs(n[2]!)]);
      continue;
    }
    if (open?.kind === "ul" || open?.kind === "ol") open.items[open.items.length - 1]!.push(runs(line.trim()));
    else if (open?.kind === "p") open.lines.push(runs(line.trim()));
    else out.push((open = { kind: "p", lines: [runs(line.trim())] }));
  }
  return out;
}

export function runs(text: string): Run[] {
  const out: Run[] = [];
  let at = 0;
  for (const m of text.matchAll(INLINE)) {
    const i = m.index ?? 0;
    if (i > at) out.push({ text: text.slice(at, i) });
    if (m[1] !== undefined) out.push({ text: m[1], code: true });
    else out.push(...runs(m[2]!).map((r) => ({ ...r, bold: true })));
    at = i + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

const pairs = (s: string) => (s.match(/\*\*/g) ?? []).length;

/** The opening sentence of a summary, for the answer's lead line, and the rest of the text. The sentence ends
 * outside bold; bold around the whole sentence is dropped, since the lead is bold already; a summary that opens
 * with a list or a heading has no lead sentence. */
export function leadSentence(text: string): [string, string] {
  const t = text.trim();
  const end = /\n[ \t]*\n|\n(?=[ \t]*(?:[-*+•]|\d{1,9}[.)])[ \t]+)/.exec(t);
  const para = end ? t.slice(0, end.index) : t;
  const first = para.split("\n")[0]!;
  if (BULLET.test(first) || NUMBER.test(first) || HEADING.test(first)) return ["", t];
  for (const m of para.matchAll(/[.!?](?:\*\*)?(?=\s|$)/g)) {
    const at = (m.index ?? 0) + m[0].length;
    if (pairs(para.slice(0, at)) % 2 === 0) return [unwrap(para.slice(0, at)), t.slice(at).trim()];
  }
  return [unwrap(para), t.slice(para.length).trim()];
}

function unwrap(s: string): string {
  const m = /^\*\*(?=\S)([\s\S]*\S)\*\*$/.exec(s.trim());
  return m && !m[1]!.includes("**") ? m[1]! : s.trim();
}
