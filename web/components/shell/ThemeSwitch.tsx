"use client";

import { useTransition } from "react";
import { setTheme } from "@/lib/actions/theme";

const NEXT: Record<string, string> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<string, string> = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" };
const GLYPH: Record<string, string> = { system: "◐", light: "○", dark: "●" };

/** Cycles the theme: system, light, dark. The choice is a cookie the server reads, so a reload keeps it. */
export function ThemeSwitch({ theme }: { theme: string }) {
  const [pending, start] = useTransition();
  return (
    <button type="button" className="nav" style={{ border: 0, background: "transparent", cursor: "pointer", font: "inherit", fontSize: 13, marginLeft: "auto" }}
      disabled={pending} onClick={() => start(() => setTheme(NEXT[theme] ?? "system"))} aria-label={`${LABEL[theme] ?? LABEL.system}. Switch theme`} title="Switch between the system theme, light and dark">
      <span aria-hidden="true">{GLYPH[theme] ?? GLYPH.system}</span>
    </button>
  );
}
