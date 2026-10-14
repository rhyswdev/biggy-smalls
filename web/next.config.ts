import type { NextConfig } from "next";

// The API's address is read here, on the server, and nowhere in client code (ruling U7). The browser reaches
// the event stream through this rewrite, so it never learns where the API is.
const api = process.env.SMALLS_API_URL ?? `http://127.0.0.1:${process.env.SMALLS_API_PORT ?? "7318"}`;

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // A server-sent event stream must reach the browser one event at a time; compression would buffer it.
  compress: false,
  async rewrites() {
    return [{ source: "/events/:id", destination: `${api}/chats/:id/events` }];
  },
};

export default config;
