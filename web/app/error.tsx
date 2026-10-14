"use client";

// The framework requires this file to be a client component. It only shows the message and offers a retry.
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main role="alert" style={{ maxWidth: 640, margin: "64px auto", padding: "0 24px", display: "flex", flexDirection: "column", gap: 12 }}>
      <h1 className="title" style={{ margin: 0 }}>Something went wrong</h1>
      <p className="body">{error.message || "The page could not be shown."}</p>
      <button type="button" className="sm-btn sm-btn-secondary" onClick={reset} style={{ alignSelf: "flex-start" }}>Try again</button>
    </main>
  );
}
