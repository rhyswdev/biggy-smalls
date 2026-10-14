// After a build: the browser's assets must hold neither the API's address nor the name of any key variable
// (docs/decisions/ui-build.md section 7.3).
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const assets = join(root, ".next", "static");
const port = process.env.SMALLS_API_PORT ?? "7318";
const address = process.env.SMALLS_API_URL ?? `http://127.0.0.1:${port}`;
const example = join(root, "..", ".env.example");
const keyNames = existsSync(example)
  ? readFileSync(example, "utf8").split("\n").map((l) => l.match(/^\s*([A-Z][A-Z0-9_]*)\s*=/)?.[1]).filter(Boolean)
  : [];
const forbidden = [address, address.replace(/^https?:\/\//, ""), `:${port}`, "SMALLS_API_URL", "SMALLS_API_PORT", ...keyNames,
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY"];

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

if (!existsSync(assets)) {
  console.error("client assets: no .next/static; build first");
  process.exit(1);
}
const files = walk(assets).filter((p) => /\.(js|css|html|json|map)$/.test(p));
const found = [];
for (const path of files) {
  const text = readFileSync(path, "utf8");
  for (const s of forbidden) if (text.includes(s)) found.push(`${path.slice(root.length)} holds ${JSON.stringify(s)}`);
}
if (found.length) {
  console.error(found.join("\n"));
  process.exit(1);
}
console.log(`client assets: ${files.length} files hold no API address and none of ${new Set(forbidden).size} key or address names`);
