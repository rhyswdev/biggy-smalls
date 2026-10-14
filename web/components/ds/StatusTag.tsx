import { cx } from "./util";

/** Every status carries a glyph and a word, never colour alone. */
export const STATUS: Record<string, [tone: string, glyph: string, word: string]> = {
  leading: ["action", "●", "Leading"],
  open: ["neutral", "○", "Open"],
  rejected: ["muted", "✕", "Rejected"],
  awaiting_approval: ["attention", "◆", "Awaiting approval"],
  approved: ["ok", "✓", "Approved"],
  edited: ["ok", "✓", "Approved with edits"],
  auto_approved: ["muted", "✓", "Auto-approved"],
  skipped: ["muted", "–", "Skipped"],
  rejected_by_user: ["danger", "✕", "Rejected by IC"],
  running: ["neutral", "…", "Running"],
  ok: ["ok", "✓", "Returned"],
  error: ["danger", "!", "Error"],
  investigating: ["attention", "◆", "Investigating"],
  resolved: ["ok", "✓", "Resolved"],
};

export function StatusTag({ status, children, className }: { status: string; children?: React.ReactNode; className?: string }) {
  const [tone, glyph, word] = STATUS[status] ?? ["neutral", "·", status];
  return (
    <span className={cx("sm-tag", `sm-tag-${tone}`, className)}>
      <span className="sm-tag-glyph" aria-hidden="true">{glyph}</span>
      {children ?? word}
    </span>
  );
}
