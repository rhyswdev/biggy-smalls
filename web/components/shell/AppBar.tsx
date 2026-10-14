import Link from "next/link";
import { cookies } from "next/headers";
import { ThemeSwitch } from "./ThemeSwitch";

/** The app bar every screen starts with: the brand, the Dashboard link and New chat. A screen may put its own
 * incident bar in the middle. */
export async function AppBar({ current, children }: { current?: "dashboard"; children?: React.ReactNode }) {
  const theme = (await cookies()).get("smalls-theme")?.value ?? "system";
  return (
    <header className="appbar">
      <Link className="brand" href="/">Smalls</Link>
      <nav aria-label="Main" style={{ display: "flex", gap: 4 }}>
        <Link className="nav" href="/" aria-current={current === "dashboard" ? "page" : undefined}>Dashboard</Link>
        {children ? <Link className="nav" href="/chats/new">New chat</Link> : null}
      </nav>
      {children ?? <span className="small muted" style={{ flex: "1 1 240px" }}>Incident investigation over raw operational evidence</span>}
      <ThemeSwitch theme={theme} />
      {children ? null : (
        <Link className="lbtn" href="/chats/new">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
          New chat
        </Link>
      )}
    </header>
  );
}
