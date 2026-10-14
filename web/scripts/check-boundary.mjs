// The rules the build checks (docs/decisions/ui-build.md section 7.3): no client logic in a page or a layout,
// no API route, no public environment variable, no raw HTML, and the API client stays on the server.
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative, sep } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const SKIP = new Set(["node_modules", ".next", "test-results", "playwright-report"]);
const problems = [];

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    if (SKIP.has(name)) return [];
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

const files = walk(root).filter((p) => /\.(ts|tsx|mjs|js)$/.test(p) && !p.endsWith(`lib${sep}api-types.ts`));
const isClient = (text) => /^\s*(['"])use client\1/m.test(text.split("\n").slice(0, 5).join("\n"));

for (const path of files) {
  const rel = relative(root, path);
  const text = readFileSync(path, "utf8");
  const self = rel === `scripts${sep}check-boundary.mjs`;
  if (/^app[\\/].*(page|layout)\.tsx$/.test(rel) && isClient(text)) problems.push(`${rel}: a page or layout must be a server component`);
  if (!self && text.includes("NEXT_PUBLIC_")) problems.push(`${rel}: no NEXT_PUBLIC_ variable; nothing the server knows goes to the browser`);
  if (!self && text.includes("dangerouslySetInnerHTML")) problems.push(`${rel}: no raw HTML; evidence is rendered as text`);
  if (isClient(text)) {
    if (/from\s+["']@\/lib\/api["']/.test(text)) problems.push(`${rel}: a client component must not import the API client`);
    if (/process\.env/.test(text)) problems.push(`${rel}: a client component must not read the environment`);
  }
}
if (existsSync(join(root, "app", "api"))) problems.push("app/api: there is no API route; the API is the Python service");
const client = readFileSync(join(root, "lib", "api.ts"), "utf8");
if (!/^import "server-only";/m.test(client)) problems.push('lib/api.ts: must import "server-only"');

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`boundary: ${files.length} files checked, all rules hold`);
