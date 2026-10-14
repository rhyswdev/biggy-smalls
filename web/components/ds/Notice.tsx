import { cx } from "./util";

const GLYPH: Record<string, string> = { info: "·", clamped: "⇥", empty: "∅", error: "!" };

/** Tool error states are text, never crashes. */
export function Notice({ tone = "info", children, className }: { tone?: "info" | "clamped" | "empty" | "error"; children: React.ReactNode; className?: string }) {
  return (
    <div className={cx("sm-notice", `sm-notice-${tone}`, className)} role={tone === "error" ? "alert" : undefined}>
      <span className="sm-notice-glyph" aria-hidden="true">{GLYPH[tone] ?? "·"}</span>
      <span>{children}</span>
    </div>
  );
}
