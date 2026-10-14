import Link from "next/link";

export default function NotFound() {
  return (
    <main style={{ maxWidth: 640, margin: "64px auto", padding: "0 24px", display: "flex", flexDirection: "column", gap: 12 }}>
      <h1 className="title" style={{ margin: 0 }}>Not found</h1>
      <p className="body">There is nothing at this address. The chat may have belonged to an earlier start of the app: nothing is kept across a restart.</p>
      <Link href="/">Back to the dashboard</Link>
    </main>
  );
}
