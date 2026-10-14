import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import { cookies } from "next/headers";
import "@/styles/tokens.css";
import "@/styles/bundle.css";
import "@/styles/theme.css";
import "@/styles/screens.css";

const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-sans", display: "swap" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-mono", display: "swap" });

export const metadata: Metadata = { title: "Smalls", description: "Incident investigation over raw operational evidence" };
export const dynamic = "force-dynamic";

const THEMES = new Set(["light", "dark", "system"]);

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const chosen = (await cookies()).get("smalls-theme")?.value ?? "system";
  const theme = THEMES.has(chosen) ? chosen : "system";
  return (
    <html lang="en" data-theme={theme} className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
