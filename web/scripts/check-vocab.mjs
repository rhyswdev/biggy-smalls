// The v4 vocabulary check (docs/v4-plan.md section 4): no user-facing "read"/"reads", "leading", "owner" or
// "incident commander" in the web app. It parses every source file under app/, components/ and lib/ with the
// TypeScript compiler and looks only at text a person can see: JSX text, and string and template literals
// outside imports and outside attributes that never show (className, href, key, id and the like). A literal
// that is one lower-case token is an identifier or an enum value (the report's `leading` status, a call's
// `read` kind) and is skipped; "Read-only" is a different sense and is allowed.
//
// Screens not yet moved to v4 are listed in PENDING; each screen's commit takes its files off the list, and the
// last commit empties it.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";

const ROOT = new URL("..", import.meta.url).pathname;
const DIRS = ["app", "components", "lib"];
const WORDS = /\b(reads?|leading|owner|incident commander)\b/i;
const ALLOWED = [/\bread-only\b/gi];
const HIDDEN_ATTRS = new Set(["className", "href", "key", "id", "htmlFor", "type", "role", "name", "rel", "target", "method", "action",
  "autoComplete", "inputMode", "src", "status", "tone", "variant", "size", "kind", "data-slot", "aria-controls", "aria-labelledby",
  "aria-describedby", "aria-current", "aria-haspopup"]);
const SKIP_FILES = [/lib\/api-types\.ts$/, /lib\/openapi\.json$/];

// Files whose screens are not yet on v4, relative to web/. Emptied by the sweep (v4 plan row 7).
export const PENDING = new Set([
  "app/design/page.tsx", "components/chat/IncidentBar.tsx", "components/shell/AppBar.tsx",
]);

function files(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...files(path));
    else if (/\.(ts|tsx)$/.test(name)) out.push(path);
  }
  return out;
}

function attrName(node) {
  for (let p = node.parent; p; p = p.parent) {
    if (ts.isJsxAttribute(p)) return p.name.getText();
    if (ts.isJsxElement(p) || ts.isJsxSelfClosingElement(p) || ts.isBlock(p)) return null;
  }
  return null;
}

function inImport(node) {
  for (let p = node.parent; p; p = p.parent) if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isExternalModuleReference(p)) return true;
  return false;
}

export function hits(text, fileName = "x.tsx") {
  const source = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true, fileName.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found = [];
  const check = (node, value) => {
    let shown = value;
    for (const ok of ALLOWED) shown = shown.replace(ok, "");
    const counted = ts.isCallExpression(node.parent) && /^plural$/.test(node.parent.expression.getText());   // plural(n, "read") is shown
    if (!counted && /^[a-z0-9_.:/#?=&@-]*$/.test(value.trim())) return;          // one lower-case token: an identifier or an enum value
    const m = WORDS.exec(shown);
    if (m) found.push({ line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1, word: m[0], text: value.trim().slice(0, 80) });
  };
  const visit = (node) => {
    if (ts.isJsxText(node)) check(node, node.text);
    else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      const attr = attrName(node);
      const hidden = attr && (HIDDEN_ATTRS.has(attr) || attr.startsWith("data-"));
      const key = ts.isPropertyAssignment(node.parent) && node.parent.name === node;          // an object key, not shown
      if (!inImport(node) && !hidden && !key) check(node, node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  let bad = 0;
  for (const dir of DIRS) {
    for (const path of files(join(ROOT, dir))) {
      const rel = relative(ROOT, path);
      if (SKIP_FILES.some((r) => r.test(rel)) || PENDING.has(rel)) continue;
      for (const h of hits(readFileSync(path, "utf8"), path)) {
        bad++;
        console.error(`${rel}:${h.line}: "${h.word}" in ${JSON.stringify(h.text)}`);
      }
    }
  }
  if (bad) {
    console.error(`check-vocab: ${bad} user-facing string(s) use an old word (docs/v4-plan.md section 4).`);
    process.exit(1);
  }
  console.log("check-vocab: no old word in the v4 screens");
}
