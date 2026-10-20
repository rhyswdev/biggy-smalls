// The trigger's header: "From: <name> (<role>)" and "Time: <time>" lines before the note's text, as the
// API's reader parses it. The bubbles show the author and the time themselves, so the text goes without them.

export interface NoteHead { author: string | null; role: string | null; time: string | null; body: string }

export function noteHead(text: string): NoteHead {
  const lines = text.split("\n");
  let author: string | null = null, role: string | null = null, time: string | null = null, i = 0;
  for (; i < lines.length; i++) {
    const m = /^(From|Time):\s*(.+)$/.exec(lines[i]!.trim());
    if (!m) break;
    if (m[1] === "From") {
      const who = /^([^(]+?)\s*(?:\(([^)]*)\))?$/.exec(m[2]!.trim());
      author = who ? who[1]!.trim() : m[2]!.trim();
      role = who?.[2]?.trim() || null;
    } else time = m[2]!.trim();
  }
  return { author, role, time, body: lines.slice(i).join("\n").trim() };
}
