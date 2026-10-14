import "server-only";

// The one way the web app talks to the API: on the server, never from the browser (ruling U7). The address
// comes from the server's environment and is never sent to a client.
const BASE = process.env.SMALLS_API_URL ?? `http://127.0.0.1:${process.env.SMALLS_API_PORT ?? "7318"}`;

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
}

export class ApiError extends Error {
  constructor(readonly status: number, readonly problem: Problem | null) {
    super(problem?.detail || problem?.title || `The API answered ${status}`);
  }
}

export async function api<T>(path: string, init: { method?: string; json?: unknown } = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: init.method ?? (init.json === undefined ? "GET" : "POST"),
      headers: init.json === undefined ? undefined : { "content-type": "application/json" },
      body: init.json === undefined ? undefined : JSON.stringify(init.json),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(503, { type: "about:blank", title: "API unreachable", status: 503, detail: "The Smalls API is not answering. Is it running?" });
  }
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as Problem | null;
    throw new ApiError(response.status, problem);
  }
  return (await response.json()) as T;
}

/** The same call, answering null for a 404 instead of throwing. */
export async function maybe<T>(path: string): Promise<T | null> {
  try {
    return await api<T>(path);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}
