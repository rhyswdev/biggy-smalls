export type BannerTone = "action" | "attention" | "neutral" | "ok";

const GLYPH: Record<BannerTone, string> = { action: "›", attention: "◆", neutral: "·", ok: "✓" };

/** The one-line status under the header: a tone, a glyph (never colour alone), a bold title, one sentence, an
 * optional second line and an optional button. Each screen passes its own words. */
export function StatusBanner({ tone, title, text, sub, glyph, children }: {
  tone: BannerTone; title: string; text?: React.ReactNode; sub?: React.ReactNode; glyph?: string; children?: React.ReactNode;
}) {
  return (
    <div className={`v-banner now-${tone}`} role="status" aria-live="polite">
      <span className="now-g" aria-hidden="true">{glyph ?? GLYPH[tone]}</span>
      <span className="now-t">
        <span><b>{title}</b>{text ? <> {text}</> : null}</span>
        {sub ? <span className="now-sub">{sub}</span> : null}
      </span>
      {children}
    </div>
  );
}
